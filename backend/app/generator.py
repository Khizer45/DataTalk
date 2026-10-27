"""Turns a question into SQL: the AI model while today's quota lasts, otherwise the demo rules."""
import json
import re
from urllib.request import Request, urlopen

from .config import settings
from .limits import DailyQuota
from .rules import UnsupportedQuestionError, rule_based_sql


class LLMError(RuntimeError):
    pass


_llm_quota = DailyQuota(settings.llm_daily_limit)


def llm_enabled() -> bool:
    return bool(settings.llm_api_key and settings.llm_model)


def generate_sql(question: str, schema_context: str) -> tuple[str, str, str]:
    """Return (sql, generator, explanation). Uses the LLM while today's quota lasts, otherwise the demo rules."""
    if llm_enabled():
        if _llm_quota.try_consume():
            return _llm_sql(question, schema_context)
        try:
            sql, explanation = rule_based_sql(question)
        except UnsupportedQuestionError as exc:
            raise UnsupportedQuestionError(
                "This demo has reached today's limit for AI-generated answers. The suggested questions "
                "still work in demo mode; open-ended questions will work again tomorrow."
            ) from exc
        return sql, "rules", explanation
    sql, explanation = rule_based_sql(question)
    return sql, "rules", explanation


def _llm_sql(question: str, schema_context: str) -> tuple[str, str, str]:
    prompt = (
        "Convert the user's question into exactly one PostgreSQL SELECT query. "
        "Use only the tables and columns in the schema below. Never write or modify data. "
        "Return SQL only, without markdown. Keep output to at most 200 rows.\n\n"
        f"Schema:\n{schema_context}\n\nQuestion: {question}"
    )
    body = json.dumps({"model": settings.llm_model, "messages": [
        {"role": "system", "content": "You write safe, schema-grounded, read-only PostgreSQL queries."},
        {"role": "user", "content": prompt},
    ], "temperature": 0}).encode()
    request = Request(f"{settings.llm_base_url}/chat/completions", data=body, headers={
        "Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json",
    }, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
        content = payload["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:sql)?\s*|\s*```$", "", content, flags=re.IGNORECASE).strip()
        return content, "llm", "Generated from the live database schema."
    except Exception as exc:
        raise LLMError("The configured LLM could not generate a query. Check the endpoint, model, and API key.") from exc

