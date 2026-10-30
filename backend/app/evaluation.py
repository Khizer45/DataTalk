"""Measure how often DataTalk answers correctly, using tagged questions with reference SQL.

    python -m app.evaluation                       # all cases, writes eval/results/<date>-<mode>.json
    python -m app.evaluation --category dates      # one category
    python -m app.evaluation --out my-run.json

A case passes when the result rows match a reference query (order-insensitive unless the case says
otherwise; extra columns are allowed), or, for questions that should not be answered, when DataTalk
declines, the SQL is rejected, or the model asks a clarifying question. Each question goes through the
same pipeline as the app, including the one retry. Matching values is not the same as being right for
the right reason, so read the per-case report as well as the totals.
"""
import argparse
import json
import re
import statistics
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .database import execute_read_query
from .generator import LLMError, answer_question, llm_enabled
from .rules import UnsupportedQuestionError
from .safety import UnsafeQueryError, validate_read_query
from .schema_info import describe_schema, get_table_row_counts, get_tables

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
ROW_LIMIT = 1000
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def normalize_value(value):
    """Make equivalent answers compare equal: 5 vs 5.00, a month as '2025-03' vs a date, '2024' vs 2024."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return round(float(value), 2)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if _MONTH.match(text):
            return f"{text}-01"
        if _NUMBER.match(text):
            return round(float(text), 2)
        return text
    return str(value)


def results_match(actual: list[dict], expected: list[dict], order_matters: bool = False) -> bool:
    """True when every expected column appears in the actual rows, lined up row by row.

    Column names are ignored (models alias freely) and extra actual columns are allowed.
    """
    if len(actual) != len(expected):
        return False
    if not expected:
        return True
    expected_columns = [[normalize_value(row[key]) for row in expected] for key in expected[0]]
    actual_columns = [[normalize_value(row[key]) for row in actual] for key in actual[0]]
    canonical = (lambda values: values) if order_matters else (lambda values: sorted(values, key=repr))

    mapping: list[int] = []
    for column in expected_columns:
        match = next((i for i, candidate in enumerate(actual_columns)
                      if i not in mapping and canonical(candidate) == canonical(column)), None)
        if match is None:
            return False
        mapping.append(match)
    # Each column matches on its own; check that the matched columns also pair up the same way per row.
    got = [tuple(actual_columns[i][row] for i in mapping) for row in range(len(actual))]
    want = [tuple(column[row] for column in expected_columns) for row in range(len(expected))]
    return got == want if order_matters else sorted(got, key=repr) == sorted(want, key=repr)


def _run_reference(sql: str, allowed: list[str]) -> list[dict]:
    return execute_read_query(validate_read_query(sql, allowed, ROW_LIMIT))


def run_case(case: dict, schema: str, allowed: list[str]) -> dict:
    # attempts gets one {"sql", "problem"} entry per model call, so a retry and what prompted it show in the report.
    record = {"id": case["id"], "category": case["category"], "question": case["question"],
              "expected": case["expected"], "generator": None, "sql": None, "assumptions": [], "attempts": [],
              "detail": None}
    started = time.perf_counter()
    try:
        answer = answer_question(case["question"], schema, allowed, ROW_LIMIT, execute_read_query,
                                 attempts=record["attempts"])
    except UnsupportedQuestionError:
        outcome = "declined"
    except UnsafeQueryError as exc:
        outcome, record["detail"] = "rejected", str(exc)
    except LLMError as exc:
        outcome, record["detail"] = "generation_error", str(exc)
    except SQLAlchemyError as exc:
        outcome, record["detail"] = "execution_error", str(getattr(exc, "orig", exc)).splitlines()[0]
    else:
        record.update(generator=answer.generator, sql=answer.sql, assumptions=answer.assumptions)
        if answer.clarification:
            # Nothing ran: right for an ambiguous question, a miss (but not a wrong answer) for an answerable one.
            outcome, record["detail"] = "clarification", answer.clarification["question"]
        elif case["expected"] == "answer":
            references = [case["sql"], *case.get("alt_sql", [])]
            matched = any(results_match(answer.rows, _run_reference(sql, allowed), case.get("order_matters", False))
                          for sql in references)
            outcome = "correct" if matched else "wrong_result"
            record["detail"] = None if matched else f"{len(answer.rows)} rows returned"
        else:
            outcome = "answered"
    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    record["outcome"] = outcome
    record["passed"] = (outcome == "correct" if case["expected"] == "answer"
                        else outcome in ("declined", "rejected", "clarification"))
    return record


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def summarize(results: list[dict]) -> dict:
    answerable = [r for r in results if r["expected"] == "answer"]
    decline = [r for r in results if r["expected"] == "decline"]
    attempted = [r for r in answerable if r["outcome"] in ("correct", "wrong_result", "execution_error")]
    executed = [r for r in answerable if r["outcome"] in ("correct", "wrong_result")]
    latencies = sorted(r["latency_ms"] for r in results)
    by_category: dict[str, dict] = {}
    for r in results:
        entry = by_category.setdefault(r["category"], {"cases": 0, "passed": 0})
        entry["cases"] += 1
        entry["passed"] += r["passed"]
    return {
        "answerable_cases": len(answerable),
        "execution_accuracy": _rate(sum(r["outcome"] == "correct" for r in answerable), len(answerable)),
        # Wrong rows presented as an answer: the failure users cannot spot, so it is reported on its own.
        "wrong_answer_rate": _rate(sum(r["outcome"] == "wrong_result" for r in answerable), len(answerable)),
        "valid_sql_rate": _rate(len(attempted), len(answerable)),
        "execution_success_rate": _rate(len(executed), len(answerable)),
        "declined_answerable_rate": _rate(sum(r["outcome"] == "declined" for r in answerable), len(answerable)),
        "clarified_answerable_rate": _rate(sum(r["outcome"] == "clarification" for r in answerable),
                                           len(answerable)),
        # Questions where the model's first reply could not be used and it was asked once more.
        "retry_rate": _rate(sum(len(r["attempts"]) > 1 for r in results), len(results)),
        "decline_cases": len(decline),
        "decline_accuracy": _rate(sum(r["passed"] for r in decline), len(decline)),
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None,
        "by_category": by_category,
    }


def run_evaluation(cases: list[dict]) -> dict:
    allowed = [table["name"] for table in get_tables()]
    schema = describe_schema("basic")
    results = [run_case(case, schema, allowed) for case in cases]
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": f"llm:{settings.llm_model}" if llm_enabled() else "rules",
        "dataset_rows": get_table_row_counts(allowed),
        "summary": summarize(results),
        "results": results,
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate DataTalk against tagged questions.")
    parser.add_argument("--cases", type=Path, default=EVAL_DIR / "cases.json")
    parser.add_argument("--category", help="only run cases in this category")
    parser.add_argument("--out", type=Path, help="where to write the JSON report")
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text())["cases"]
    if args.category:
        cases = [case for case in cases if case["category"] == args.category]
    report = run_evaluation(cases)
    summary = report["summary"]

    out = args.out or EVAL_DIR / "results" / f"{report['run_at'][:10]}-{re.sub(r'[^A-Za-z0-9.-]+', '-', report['mode'])}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"Mode: {report['mode']}   Cases: {len(cases)}   Report: {out}")
    print(f"Answerable questions ({summary['answerable_cases']}):")
    print(f"  execution accuracy      {_percent(summary['execution_accuracy'])}")
    print(f"  wrong answers shown     {_percent(summary['wrong_answer_rate'])}")
    print(f"  valid SQL               {_percent(summary['valid_sql_rate'])}")
    print(f"  ran without error       {_percent(summary['execution_success_rate'])}")
    print(f"  declined (should answer){_percent(summary['declined_answerable_rate']):>8}")
    print(f"  asked to clarify        {_percent(summary['clarified_answerable_rate'])}")
    print(f"Questions to decline ({summary['decline_cases']}): handled {_percent(summary['decline_accuracy'])}")
    print(f"Retried once after an unusable first reply: {_percent(summary['retry_rate'])} of questions")
    print(f"Latency p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms")
    for name, entry in sorted(summary["by_category"].items()):
        print(f"  {name:<14} {entry['passed']}/{entry['cases']}")


if __name__ == "__main__":
    main()
