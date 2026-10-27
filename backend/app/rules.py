"""Demo mode: answers a few question types without an AI model, and declines everything else."""
import re


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


def rule_based_sql(question: str) -> tuple[str, str]:
    """Return (sql, explanation) for a recognised question, or raise UnsupportedQuestionError."""
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
