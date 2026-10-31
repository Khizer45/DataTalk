"""Demo mode: answers a few question types without an AI model, and declines everything else."""
import re

from .compose import Decline, compose_sql


SAMPLE_QUESTIONS = [
    "What was total revenue by month?",
    "Show the top 5 products by revenue",
    "How many orders are pending?",
    "Show revenue by customer country",
    "List the 10 most recent orders with customer names",
    "What was the revenue by sales channel in 2025?",
    "Which 5 customers spent the most?",
    "How many orders were cancelled in March 2025?",
    "Which products have never been ordered?",
]

# Qualifiers the rule-based mode cannot honour. Answering without them would silently return the wrong data.
# Single years and months ("in 2025", "in March 2025") are understood; ranges and relative dates are not.
_UNSUPPORTED_QUALIFIERS = re.compile(
    r"\b(quarter|quarterly|q[1-4]|week|weekly|weekend|weekends|yesterday|today|since|between|before|after|until|"
    r"ytd|(last|this|past|previous|next) (\d+ )?(month|quarter|year|week|day)s?)\b"
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


def _never_ordered_products(_: re.Match) -> tuple[str, str]:
    return ("SELECT p.name AS product, p.category, p.unit_price FROM products p "
            "WHERE NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.product_id = p.id) ORDER BY p.name"), \
        "Lists products that appear in no order at all."


def _never_ordered_customers(_: re.Match) -> tuple[str, str]:
    return ("SELECT c.name AS customer, c.country, c.segment, c.created_at FROM customers c "
            "WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id) ORDER BY c.name"), \
        "Lists customers who have never placed an order."


def _price_ranking(match: re.Match) -> tuple[str, str]:
    cheapest = bool(match.group("cheap"))
    return (f"SELECT name AS product, category, unit_price FROM products "
            f"ORDER BY unit_price {'ASC' if cheapest else 'DESC'}, name LIMIT {_limit(match, 5)}"), \
        f"Ranks products by list price, {'cheapest' if cheapest else 'most expensive'} first."


def _price_list(_: re.Match) -> tuple[str, str]:
    return "SELECT name AS product, category, unit_price FROM products ORDER BY category, unit_price", \
        "Lists every product with its category and list price."


def _unshipped(_: re.Match) -> tuple[str, str]:
    return "SELECT COUNT(*) AS orders FROM orders WHERE shipped_date IS NULL", \
        "Counts orders of any status that have no shipped date yet."


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
    (r"((which|what|list|show( me)?) )?(the )?products( that| which)? (have |has )?never been (ordered|sold|bought)"
     r"|((list|show)( me)? )?(the )?products with no (orders|sales)", _never_ordered_products),
    (r"((which|what|list|show( me)?) )?(the )?customers( that| who)? (have )?never (placed|made) (an|any) orders?"
     r"|((which|what|list|show( me)?) )?(the )?customers( that| who)? (have )?never ordered( anything)?"
     r"|((list|show)( me)? )?(the )?customers (with|who have) no orders", _never_ordered_customers),
    (r"((list|show|what are)( me)? )?(the )?(?P<n>\d{1,3} )?(most expensive|(?P<cheap>cheapest|least expensive)) products?",
     _price_ranking),
    (r"((list|show|what are)( me)? )?(all )?(the )?(products( with| and)( their)? prices|product prices|price list)",
     _price_list),
    (r"how many orders (have|has) (no shipped date|not (been )?shipped( yet)?)|how many orders are (not shipped|unshipped)"
     r"( yet)?", _unshipped),
)]


def _normalize(question: str) -> str:
    q = " ".join(question.casefold().split())
    q = re.sub(r"^(please|can you|could you) ", "", q)
    return q.rstrip("?.! ")


def rule_based_sql(question: str) -> tuple[str, str]:
    """Return (sql, explanation) for a recognised question, or raise UnsupportedQuestionError.

    Fixed question types are tried first, then questions built from known pieces (compose.py). Either way the
    whole question must be understood; nothing is dropped silently.
    """
    q = _normalize(question)
    if _UNSUPPORTED_QUALIFIERS.search(q):
        raise UnsupportedQuestionError(
            "Demo mode understands a single year or month (“in 2025”, “in March 2025”) but not date ranges, "
            "quarters, weeks or relative dates like “last month”. Rephrase with a year or month, or configure "
            "an LLM for open-ended questions."
        )
    for pattern, build in _RULES:
        match = pattern.fullmatch(q)
        if match:
            return build(match)
    try:
        return compose_sql(question)
    except Decline as exc:
        raise UnsupportedQuestionError(str(exc)) from exc
    except LookupError:
        pass
    raise UnsupportedQuestionError(
        "Demo mode didn't understand every part of this question, and it never guesses. It knows revenue, orders, "
        "average order value, quantity sold, customers and products, broken down by month, year, country, channel, "
        "status, category, segment, product or customer, and filtered by a year or month, country, channel, "
        "segment, status or category. Try one of the suggestions, or configure an LLM for open-ended questions."
    )
