import json
from datetime import date, datetime
from decimal import Decimal

from app.evaluation import EVAL_DIR, normalize_value, results_match, run_case, summarize


def test_equivalent_values_compare_equal():
    assert normalize_value(Decimal("5.00")) == normalize_value(5) == normalize_value("5")
    assert normalize_value("2025-03") == normalize_value(date(2025, 3, 1)) == normalize_value(datetime(2025, 3, 1))
    assert normalize_value(None) is None


def test_row_order_is_ignored_unless_it_matters():
    expected = [{"country": "A", "n": 1}, {"country": "B", "n": 2}]
    reversed_rows = list(reversed(expected))
    assert results_match(reversed_rows, expected)
    assert not results_match(reversed_rows, expected, order_matters=True)


def test_column_names_and_extra_columns_are_allowed():
    expected = [{"country": "A"}, {"country": "B"}]
    actual = [{"c": "A", "revenue": 10}, {"c": "B", "revenue": 20}]
    assert results_match(actual, expected)


def test_columns_must_pair_up_row_by_row():
    expected = [{"country": "A", "n": 1}, {"country": "B", "n": 2}]
    swapped = [{"country": "A", "n": 2}, {"country": "B", "n": 1}]
    assert not results_match(swapped, expected)


def test_row_count_must_match():
    assert not results_match([{"n": 1}], [{"n": 1}, {"n": 1}])


def test_cases_file_is_well_formed():
    cases = json.loads((EVAL_DIR / "cases.json").read_text())["cases"]
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["expected"] in ("answer", "decline")
        assert case["question"].strip()
        assert ("sql" in case) == (case["expected"] == "answer")


def test_a_clarification_counts_as_declining_not_as_a_wrong_answer(model):
    clarification = {"clarification": {"question": "Best by revenue or by number of orders?",
                                       "options": ["Top 5 customers by revenue?", "Top 5 customers by orders?"]}}
    model["Who are our best customers?"] = clarification
    model["How many customers are there?"] = clarification
    schema, allowed = "customers(id, name, country)", ["customers"]
    declined = run_case({"id": "a", "category": "ambiguous", "expected": "decline",
                         "question": "Who are our best customers?"}, schema, allowed)
    missed = run_case({"id": "b", "category": "aggregation", "expected": "answer",
                       "question": "How many customers are there?", "sql": "SELECT COUNT(*) FROM customers"},
                      schema, allowed)
    assert declined["outcome"] == missed["outcome"] == "clarification"
    assert declined["passed"] and not missed["passed"] and missed["sql"] is None
    summary = summarize([declined, missed])
    assert summary["decline_accuracy"] == 1 and summary["wrong_answer_rate"] == 0
    assert summary["clarified_answerable_rate"] == 1 and summary["retry_rate"] == 0
