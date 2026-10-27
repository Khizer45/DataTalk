import json
import re
from urllib.request import Request, urlopen

from sqlalchemy import inspect

from .config import settings
from .database import get_engine


SAMPLE_QUESTIONS = [
    "What was total revenue by month?",
    "Show the top 5 products by revenue",
    "How many orders are pending?",
    "Show revenue by customer country",
    "List the 10 most recent orders with customer names",
]

# Qualifiers the rule-based mode cannot honour. Answering without them would silently return the wrong data.
_UNSUPPORTED_QUALIFIERS = re.compile(
    r"\b(quarter|week|year|yesterday|today|since|between|before|after|during|20\d\d|"
    r"(last|this|past|previous|next) (month|quarter|year|week|day)s?)\b"
)


class UnsupportedQuestionError(ValueError):
    pass


class LLMError(RuntimeError):
    pass


def get_schema_context() -> tuple[list[dict], str]:
    inspector = inspect(get_engine())
    existing = set(inspector.get_table_names())
    tables = []
    for name in settings.exposed_tables:
        if name not in existing:
            continue
        columns = [{"name": c["name"], "type": str(c["type"])} for c in inspector.get_columns(name)]
        tables.append({"name": name, "columns": columns})
    context = "\n".join(f"{table['name']}({', '.join(c['name'] for c in table['columns'])})" for table in tables)
    return tables, context


def _requested_count(q: str, default: int) -> int:
    match = re.search(r"\b(\d{1,3})\b", q)
    return max(1, min(int(match.group(1)), 500)) if match else default


def _fallback_sql(question: str) -> tuple[str, str]:
    q = question.casefold()
    if _UNSUPPORTED_QUALIFIERS.search(q):
        raise UnsupportedQuestionError(
            "Demo mode can't filter by date ranges yet. Try one of the suggested questions, "
            "or configure an LLM for open-ended questions."
        )
    if "pending" in q and "order" in q and any(word in q for word in ("how many", "count", "number")):
        return "SELECT COUNT(*) AS pending_orders FROM orders WHERE status = 'pending'", "Counts orders currently marked pending."
    if ("top" in q or "most" in q) and "product" in q and ("revenue" in q or "sales" in q):
        limit = _requested_count(q, 5)
        return ("SELECT p.name AS product, SUM(oi.quantity * oi.unit_price) AS revenue "
                "FROM order_items oi JOIN products p ON p.id = oi.product_id "
                f"GROUP BY p.name ORDER BY revenue DESC LIMIT {limit}"), "Ranks products by line-item revenue."
    if "country" in q and ("revenue" in q or "sales" in q):
        return ("SELECT c.country, SUM(o.total_amount) AS revenue FROM orders o "
                "JOIN customers c ON c.id = o.customer_id GROUP BY c.country ORDER BY revenue DESC"), "Groups order revenue by customer country."
    if "month" in q and ("revenue" in q or "sales" in q):
        return ("SELECT to_char(date_trunc('month', order_date), 'YYYY-MM') AS month, SUM(total_amount) AS revenue "
                "FROM orders GROUP BY 1 ORDER BY 1"), "Groups order revenue by calendar month."
    if "recent" in q and "order" in q:
        limit = _requested_count(q, 10)
        return (f"SELECT o.id, o.order_date, o.status, o.total_amount, c.name AS customer "
                f"FROM orders o JOIN customers c ON c.id = o.customer_id ORDER BY o.order_date DESC LIMIT {limit}"), "Lists the newest orders and customer names."
    if "customer" in q and ("count" in q or "how many" in q or "number" in q):
        return "SELECT COUNT(*) AS customer_count FROM customers", "Counts customers in the dataset."
    if "order" in q and "status" in q:
        return "SELECT status, COUNT(*) AS orders FROM orders GROUP BY status ORDER BY orders DESC", "Summarizes order counts by status."
    raise UnsupportedQuestionError(
        "Demo mode only understands a few question types. Try one of the suggested questions, "
        "or configure an LLM for open-ended questions."
    )


def generate_sql(question: str, schema_context: str) -> tuple[str, str, str]:
    if not (settings.llm_api_key and settings.llm_model):
        sql, explanation = _fallback_sql(question)
        return sql, "rules", explanation

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

