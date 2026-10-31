import pytest

from app.rules import SAMPLE_QUESTIONS, UnsupportedQuestionError, rule_based_sql


@pytest.mark.parametrize("question", SAMPLE_QUESTIONS)
def test_every_suggested_question_is_answered(question):
    sql, explanation = rule_based_sql(question)
    assert sql.startswith("SELECT") and explanation


def test_top_n_uses_the_requested_number():
    assert rule_based_sql("Show the top 3 products by revenue")[0].endswith("LIMIT 3")


def test_case_spacing_and_punctuation_are_ignored():
    assert rule_based_sql("  how many PENDING orders are there?? ")[0] == rule_based_sql("How many orders are pending?")[0]


@pytest.mark.parametrize("question", [
    "What is each customer's phone number?",
    "Delete all orders",
    "Which customers live in Lahore?",
    "Show revenue by month for the website channel excluding refunds",
    "How many customers are from Pakistan and India?",
    "Show me sales.",
    "Revenue by status",
    "How many customers in 2025?",
    "Which products sold the most?",
    "Drop table orders",
    "revenue by month; DROP TABLE orders",
])
def test_questions_with_unknown_parts_are_declined_not_guessed(question):
    with pytest.raises(UnsupportedQuestionError):
        rule_based_sql(question)


def test_date_ranges_get_a_specific_message():
    with pytest.raises(UnsupportedQuestionError, match="date ranges"):
        rule_based_sql("Which products generated the most revenue last quarter?")


def test_years_outside_the_data_are_explained():
    with pytest.raises(UnsupportedQuestionError, match="2024 to December 2025"):
        rule_based_sql("What was the revenue in 2023?")


# Phrasings written for these tests, not taken from the evaluation set.
@pytest.mark.parametrize("question, parts", [
    ("revenue by channel", ["o.channel", "SUM(o.total_amount)", "status = 'completed'"]),
    ("Show me the average order value by country", ["c.country", "AVG(o.total_amount)"]),
    ("Which customer spent the most in 2025?", ["c.name", "'2025-01-01'", "'2026-01-01'", "LIMIT 1"]),
    ("top 3 categories by quantity sold", ["p.category", "SUM(oi.quantity)", "LIMIT 3"]),
    ("How many orders were cancelled in March 2025?", ["status = 'cancelled'", "'2025-03-01'", "'2025-04-01'"]),
    ("how many pending orders came through the mobile app", ["status = 'pending'", "channel = 'Mobile app'"]),
    ("revenue from enterprise customers by month", ["c.segment = 'Enterprise'", "date_trunc('month'"]),
    ("revenue of software products by year", ["p.category = 'Software'", "'YYYY'"]),
    ("How many customers signed up in each month of 2025?", ["c.created_at", "'2025-01-01'"]),
    ("which 3 products made the least revenue", ["LEFT JOIN", "ASC", "LIMIT 3"]),
    ("Which products have never been ordered?", ["NOT EXISTS"]),
    ("How many orders came through the partner channel in December 2024?", ["channel = 'Partner'", "'2024-12-01'"]),
    ("customers by segment", ["c.segment", "COUNT(*)", "FROM customers"]),
])
def test_composed_questions(question, parts):
    sql, explanation = rule_based_sql(question)
    assert explanation
    for part in parts:
        assert part in sql, (part, sql)


def test_small_typos_are_corrected_and_reported():
    sql, explanation = rule_based_sql("what was total revenu by month")
    assert "date_trunc('month'" in sql and "status = 'completed'" in sql
    assert "revenu" in explanation


def test_revenue_never_includes_pending_or_cancelled_orders():
    with pytest.raises(UnsupportedQuestionError, match="completed orders"):
        rule_based_sql("revenue from cancelled orders")


def test_filter_values_are_never_taken_from_the_question_text():
    # Only known values (countries, channels, ...) become filters, so question text never reaches the SQL.
    with pytest.raises(UnsupportedQuestionError):
        rule_based_sql("How many customers are from x' OR '1'='1?")


@pytest.mark.parametrize("question", ["What was total revenue by month?", "Show the top 5 products by revenue",
                                      "Show revenue by customer country"])
def test_revenue_counts_completed_orders_only(question):
    assert "status = 'completed'" in rule_based_sql(question)[0]
