"""Answers a question: SQL from the AI model while today's quota lasts, otherwise from the demo rules.

The model replies with one JSON object, parsed strictly: {"sql", "explanation", "assumptions"} to answer, or
{"clarification": {"question", "options"}} to ask which reading of the question was meant. Its SQL is untrusted and
goes through the same validator and read-only execution as every other query. A malformed reply, SQL the validator
rejects as fixable, or a query that names an unknown column or table gets one more model call with the problem; a write
attempt never does. Every model call, a retry included, counts against LLM_DAILY_LIMIT.
"""
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from sqlalchemy.exc import DBAPIError

from .config import settings
from .errors import fixable_by_model
from .limits import DailyQuota
from .rules import UnsupportedQuestionError, rule_based_sql
from .safety import UnsafeQueryError, validate_read_query

logger = logging.getLogger("datatalk.generator")


class LLMError(RuntimeError):
    pass


class ReplyFormatError(ValueError):
    """The model's reply is not one of the two JSON forms. The message says what is wrong and is sent back on the retry."""


@dataclass
class Answer:
    generator: str                     # "llm" or "rules"
    sql: str | None = None             # the validated SQL that ran; None when the model asked a question instead
    rows: list[dict] = field(default_factory=list)
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    clarification: dict | None = None  # {"question": ..., "options": [...]}; nothing was run


MAX_MODEL_CALLS = 2  # the first reply and at most one retry
MAX_REPLY_CHARS = 12_000
_llm_quota = DailyQuota(settings.llm_daily_limit)
_UPLOAD_NEEDS_AI = ("Questions about your own data need AI mode (an LLM API key on the server). "
                    "Demo mode only answers the suggested questions about the demo shop.")
_UPLOAD_LIMIT_REACHED = ("This demo has reached today's limit for AI-generated answers, so questions about "
                         "uploaded data are paused until tomorrow.")
_UNREADABLE_REPLY = ("The AI model's reply was not in the expected format, so no query was run. Please try again or "
                     "rephrase the question.")
_FENCED = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_SYSTEM_PROMPT = """You write one read-only PostgreSQL query that answers a question about the tables in the schema.
Reply with exactly one JSON object and nothing else (no markdown, no text around it), in one of two forms.

To answer:
{"sql": "<one PostgreSQL SELECT query>", "explanation": "<one sentence: what the result shows>", \
"assumptions": ["<an interpretation you chose that the question did not state>"]}

To ask first:
{"clarification": {"question": "<what you need to know>", "options": ["<a complete question>", \
"<another complete question>"]}}

Rules:
- Use only the tables and columns in the schema. Never write or change data. Keep output to at most 200 rows.
- If the question can be read in ways that give different results (for example "top customers": by revenue or by \
number of orders?), ask first instead of guessing. Give 2 to 4 options. Each option is the user's question rewritten \
with one reading chosen, as a complete question that can be asked on its own, such as "Who are the top 5 customers by \
revenue?".
- If the tables cannot answer the question, ask first: say what is missing and offer questions the tables can answer.
- When you answer, list in "assumptions" every interpretation you chose that the question did not state, such as the \
dates "last month" refers to or how you defined a term. Use [] when there are none."""


def llm_enabled() -> bool:
    return bool(settings.llm_api_key and settings.llm_model)


def answer_question(question: str, schema_context: str, allowed_tables: list[str], row_limit: int,
                    run: Callable[[str], list[dict]], allow_rules: bool = True,
                    attempts: list[dict] | None = None) -> Answer:
    """Answer with the AI model while today's quota lasts, otherwise with the demo rules.

    Whoever writes the SQL, it is validated against allowed_tables and capped at row_limit rows, then run executes it
    read-only (the caller decides on which database and schema). allow_rules=False is for uploaded datasets and
    connected databases: the demo rules only know the demo tables. attempts, if given, receives one {"sql", "problem"}
    entry per model call.
    """
    if llm_enabled():
        if _llm_quota.try_consume():
            return _model_answer(question, schema_context, allowed_tables, row_limit, run,
                                 [] if attempts is None else attempts)
        if not allow_rules:
            raise UnsupportedQuestionError(_UPLOAD_LIMIT_REACHED)
        try:
            sql, explanation = rule_based_sql(question)
        except UnsupportedQuestionError as exc:
            raise UnsupportedQuestionError(
                "This demo has reached today's limit for AI-generated answers. The suggested questions "
                "still work in demo mode; open-ended questions will work again tomorrow."
            ) from exc
    elif not allow_rules:
        raise UnsupportedQuestionError(_UPLOAD_NEEDS_AI)
    else:
        sql, explanation = rule_based_sql(question)
    sql = validate_read_query(sql, allowed_tables, row_limit)
    return Answer("rules", sql=sql, rows=run(sql), explanation=explanation)


def _model_answer(question: str, schema_context: str, allowed_tables: list[str], row_limit: int,
                  run: Callable[[str], list[dict]], attempts: list[dict]) -> Answer:
    today = datetime.now(timezone.utc).date().isoformat()
    messages = [{"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Schema:\n{schema_context}\n\nToday's date: {today}\n\n"
                                            f"Question: {question}"}]
    calls = 0
    while True:
        calls += 1
        content = _chat(messages)
        attempt = {"sql": None, "problem": None}
        attempts.append(attempt)
        try:
            reply = parse_reply(content)
            if "clarification" in reply:
                return Answer("llm", clarification=reply["clarification"])
            attempt["sql"] = reply["sql"]
            sql = validate_read_query(reply["sql"], allowed_tables, row_limit)
            return Answer("llm", sql=sql, rows=run(sql), explanation=reply["explanation"],
                          assumptions=reply["assumptions"])
        except (ReplyFormatError, UnsafeQueryError, DBAPIError) as exc:
            attempt["problem"] = str(getattr(exc, "orig", exc)).partition("\n")[0]
            reason = _retry_reason(exc)
            if reason is None or calls >= MAX_MODEL_CALLS or not _llm_quota.try_consume():
                if isinstance(exc, ReplyFormatError):
                    raise LLMError(_UNREADABLE_REPLY) from exc
                raise
            logger.info("asking the model again: %s", reason)
            previous = content[:MAX_REPLY_CHARS] if isinstance(content, str) and content.strip() else "(empty reply)"
            messages = [*messages, {"role": "assistant", "content": previous},
                        {"role": "user", "content": f"That reply could not be used: {reason} Reply again with one "
                                                    "JSON object in one of the two forms."}]


def _retry_reason(exc: Exception) -> str | None:
    """What to tell the model when asking it again, or None when this failure must not be retried."""
    if isinstance(exc, UnsafeQueryError):
        # A write attempt is never retried: asking again could turn a request to change data into an unrelated
        # SELECT that looks like an answer.
        return str(exc) if exc.retryable else None
    if isinstance(exc, ReplyFormatError):
        return str(exc)
    return fixable_by_model(exc)


def parse_reply(content) -> dict:
    """Read a model reply strictly: exactly one of the two JSON forms, with the expected keys, types and sizes.

    The only thing accepted around the object is a single markdown code fence, which many models add anyway.
    """
    if not isinstance(content, str) or not content.strip():
        raise ReplyFormatError("The reply was empty.")
    if len(content) > MAX_REPLY_CHARS:
        raise ReplyFormatError(f"The reply was longer than {MAX_REPLY_CHARS} characters.")
    fenced = _FENCED.fullmatch(content.strip())
    try:
        reply = json.loads(fenced.group(1) if fenced else content, object_pairs_hook=_unique_keys)
    except ReplyFormatError:
        raise
    except ValueError as exc:
        raise ReplyFormatError("The reply was not one JSON object.") from exc
    if not isinstance(reply, dict):
        raise ReplyFormatError("The reply was not one JSON object.")
    if reply.keys() == {"sql", "explanation", "assumptions"}:
        assumptions = reply["assumptions"]
        if not isinstance(assumptions, list) or len(assumptions) > 5:
            raise ReplyFormatError('"assumptions" must be a list of at most 5 strings.')
        return {"sql": _text(reply["sql"], '"sql"', 8000),
                "explanation": _text(reply["explanation"], '"explanation"', 500),
                "assumptions": [_text(item, "each assumption", 300) for item in assumptions]}
    if reply.keys() == {"clarification"}:
        clarification = reply["clarification"]
        if not isinstance(clarification, dict) or clarification.keys() != {"question", "options"}:
            raise ReplyFormatError('"clarification" must be an object with exactly the keys "question" and "options".')
        options = clarification["options"]
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            raise ReplyFormatError('"options" must be a list of 2 to 4 strings.')
        # Each option is asked as a question of its own when chosen, so it must be a valid question.
        options = [_text(item, "each option", 200, shortest=3) for item in options]
        if len({option.casefold() for option in options}) < len(options):
            raise ReplyFormatError('The "options" must all be different.')
        return {"clarification": {"question": _text(clarification["question"], '"question"', 300),
                                  "options": options}}
    raise ReplyFormatError('The reply must have exactly the keys "sql", "explanation" and "assumptions", or only '
                           '"clarification".')


def _unique_keys(pairs: list[tuple]) -> dict:
    """json.loads would silently keep the last of two equal keys; a strict reader refuses them."""
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ReplyFormatError(f'The key "{key[:40]}" appears more than once.')
        seen.add(key)
    return dict(pairs)


def _text(value, name: str, longest: int, shortest: int = 1) -> str:
    if not isinstance(value, str) or not shortest <= len(value.strip()) <= longest:
        raise ReplyFormatError(f"{name[0].upper()}{name[1:]} must be text of {shortest} to {longest} characters.")
    return value.strip()


def _chat(messages: list[dict]) -> str | None:
    """One call to the OpenAI-compatible chat completions API. Returns the reply's text (None if it has none)."""
    body = json.dumps({"model": settings.llm_model, "messages": messages, "temperature": 0}).encode()
    request = Request(f"{settings.llm_base_url}/chat/completions", data=body, headers={
        "Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json",
    }, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
        return payload["choices"][0]["message"].get("content")
    except Exception as exc:
        raise LLMError("The configured LLM could not generate a query. Check the endpoint, model, and API key.") from exc
