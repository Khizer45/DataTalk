import json
import re
from urllib.request import Request, urlopen

from sqlalchemy import func, inspect, select, table

from .config import settings
from .database import get_engine
from .limits import DailyQuota


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


_llm_quota = DailyQuota(settings.llm_daily_limit)


def llm_enabled() -> bool:
    return bool(settings.llm_api_key and settings.llm_model)


def get_schema_context() -> tuple[list[dict], str]:
    inspector = inspect(get_engine())
    existing = set(inspector.get_table_names())
    tables = []
    for name in settings.exposed_tables:
        if name not in existing:
            continue
        columns = [{"name": c["name"], "type": str(c["type"])} for c in inspector.get_columns(name)]
        tables.append({"name": name, "columns": columns})
    context = "\n".join(f"{t['name']}({', '.join(c['name'] for c in t['columns'])})" for t in tables)
    return tables, context


def get_table_row_counts(table_names: list[str]) -> dict[str, int]:
    with get_engine().connect() as connection:
        return {name: connection.execute(select(func.count()).select_from(table(name))).scalar_one()
                for name in table_names}


def _limit(match: re.Match, default: int) -> int:
    return max(1, min(int(match.group("n") or default), 500))


def _pending(_: re.Match) -> tuple[str, str]:
    return "SELECT COUNT(*) AS pending_orders FROM orders WHERE status = 'pending'", "Counts orders currently marked pending."


def _top_products(match: re.Match) -> tuple[str, str]:
    return ("SELECT p.name AS product, SUM(oi.quantity * oi.unit_price) AS revenue "
            "FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id "
            f"WHERE o.status = 'completed' GROUP BY p.name ORDER BY revenue DESC LIMIT {_limit(match, 5)}"), \
        "Ranks products by revenue from completed orders."


def _revenue_by_country(_: re.Match) -> tuple[str, str]:
    return ("SELECT c.country, SUM(o.total_amount) AS revenue FROM orders o "
            "JOIN customers c ON c.id = o.customer_id WHERE o.status = 'completed' "
            "GROUP BY c.country ORDER BY revenue DESC"), "Totals completed-order revenue by customer country."


def _revenue_by_month(_: re.Match) -> tuple[str, str]:
    return ("SELECT to_char(date_trunc('month', order_date), 'YYYY-MM') AS month, SUM(total_amount) AS revenue "
            "FROM orders WHERE status = 'completed' GROUP BY 1 ORDER BY 1"), \
        "Totals completed-order revenue by calendar month."


def _recent_orders(match: re.Match) -> tuple[str, str]:
    return ("SELECT o.id, o.order_date, o.status, o.total_amount, c.name AS customer "
            "FROM orders o JOIN customers c ON c.id = o.customer_id "
            f"ORDER BY o.order_date DESC, o.id DESC LIMIT {_limit(match, 10)}"), "Lists the newest orders and customer names."


def _customer_count(_: re.Match) -> tuple[str, str]:
    return "SELECT COUNT(*) AS customer_count FROM customers", "Counts customers in the dataset."


def _status_counts(_: re.Match) -> tuple[str, str]:
    return "SELECT status, COUNT(*) AS orders FROM orders GROUP BY status ORDER BY orders DESC", \
        "Summarizes order counts by status."


# Each rule must match the whole question (after normalising case, spacing and end punctuation). A question with
# extra conditions, such as "...from Pakistan", is therefore declined rather than answered without them.
# Revenue always means completed orders; cancelled and pending orders are excluded.
_RULES = [(re.compile(pattern), build) for pattern, build in (
    (r"(how many|count|number of) (orders are pending|pending orders( are there| do we have)?)", _pending),
    (r"((show|list|what are)( me)? )?(the )?top (?P<n>\d{1,3} )?products by (revenue|sales)", _top_products),
    (r"which products (generated|made|earned|brought in) the most (revenue|sales)", _top_products),
    (r"((show|what is|what was|what's)( me)? )?(the )?(total )?(revenue|sales) (by|per|for each) (customer )?country",
     _revenue_by_country),
    (r"((show|what is|what was|what's)( me)? )?(the )?(total )?(revenue|sales) (by|per|for each) month", _revenue_by_month),
    (r"((list|show)( me)? )?(the )?(?P<n>\d{1,3} )?(most recent|latest|newest) orders( with( their)? customer names?)?",
     _recent_orders),
    (r"how many customers (are there|do we have)|(count|number) of customers|total number of customers", _customer_count),
    (r"how many orders are (there )?(in|by|per|for) each status|((show|count)( me)? )?(the )?orders by status"
     r"|order counts? by status", _status_counts),
)]


def _normalize(question: str) -> str:
    q = " ".join(question.casefold().split())
    q = re.sub(r"^(please|can you|could you) ", "", q)
    return q.rstrip("?.! ")


def _fallback_sql(question: str) -> tuple[str, str]:
    q = _normalize(question)
    if _UNSUPPORTED_QUALIFIERS.search(q):
        raise UnsupportedQuestionError(
            "Demo mode can't filter by date ranges yet. Try one of the suggested questions, "
            "or configure an LLM for open-ended questions."
        )
    for pattern, build in _RULES:
        match = pattern.fullmatch(q)
        if match:
            return build(match)
    raise UnsupportedQuestionError(
        "Demo mode only answers a few question types, phrased like the suggested questions, so it never guesses. "
        "Try one of the suggestions, or configure an LLM for open-ended questions."
    )


def generate_sql(question: str, schema_context: str) -> tuple[str, str, str]:
    """Return (sql, generator, explanation). Uses the LLM while today's quota lasts, otherwise the demo rules."""
    if llm_enabled():
        if _llm_quota.try_consume():
            return _llm_sql(question, schema_context)
        try:
            sql, explanation = _fallback_sql(question)
        except UnsupportedQuestionError as exc:
            raise UnsupportedQuestionError(
                "This demo has reached today's limit for AI-generated answers. The suggested questions "
                "still work in demo mode; open-ended questions will work again tomorrow."
            ) from exc
        return sql, "rules", explanation
    sql, explanation = _fallback_sql(question)
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

