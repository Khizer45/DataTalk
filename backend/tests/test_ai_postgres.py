"""AI mode end to end on the demo data, with the stand-in model from conftest: clarifying questions, stated
assumptions and the one retry. Validation, the read-only role and PostgreSQL's own error messages are real.

Uploads and connected databases have their retry tests next to their other isolation tests.
"""
import pytest

from app import generator, main
from app.limits import DailyQuota

TOP_CUSTOMERS = {"clarification": {
    "question": "Should customers be ranked by revenue or by number of orders?",
    "options": ["Who are the top 5 customers by revenue?", "Who are the top 5 customers by number of orders?"]}}
TOP_BY_REVENUE = ("SELECT c.name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.id "
                  "WHERE o.status = 'completed' GROUP BY c.name ORDER BY revenue DESC LIMIT 5")


def ask(client, question):
    return client.post("/api/query", json={"question": question})


@pytest.fixture
def executed(monkeypatch):
    """The SQL that reached the database, in order."""
    ran = []
    real = main.execute_read_query
    monkeypatch.setattr(main, "execute_read_query", lambda sql, **options: ran.append(sql) or real(sql, **options))
    return ran


def test_an_ambiguous_question_gets_a_clarification_and_runs_nothing(client, model, executed):
    model["Who are our top customers?"] = TOP_CUSTOMERS
    response = ask(client, "Who are our top customers?")
    assert response.status_code == 200
    body = response.json()
    assert body["clarification"] == TOP_CUSTOMERS["clarification"]
    assert body["sql"] is None and body["rows"] == [] and body["row_count"] == 0 and executed == []
    assert body["generator"] == "llm" and body["source"] == "demo" and body["assumptions"] == []


def test_choosing_an_option_asks_it_as_a_question(client, model):
    model["Who are our top customers?"] = TOP_CUSTOMERS
    model["Who are the top 5 customers by revenue?"] = {
        "sql": TOP_BY_REVENUE, "explanation": "The five customers with the most revenue.",
        "assumptions": ["Revenue counts completed orders only."]}
    option = ask(client, "Who are our top customers?").json()["clarification"]["options"][0]
    body = ask(client, option).json()
    assert body["row_count"] == 5 and body["clarification"] is None
    assert body["assumptions"] == ["Revenue counts completed orders only."]


def test_assumptions_come_back_with_the_answer(client, model):
    model["What was revenue last month?"] = {
        "sql": "SELECT COALESCE(SUM(total_amount), 0) AS revenue FROM orders WHERE status = 'completed' "
               "AND order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month' "
               "AND order_date < date_trunc('month', CURRENT_DATE)",
        "explanation": "Revenue from completed orders placed last month.",
        "assumptions": ['"Last month" means the previous calendar month (September 2026).',
                        "Revenue counts completed orders only."]}
    body = ask(client, "What was revenue last month?").json()
    # The demo data ends in December 2025, so last month has no revenue; the assumption shows why.
    assert body["rows"] == [{"revenue": 0}]
    assert body["assumptions"] == ['"Last month" means the previous calendar month (September 2026).',
                                   "Revenue counts completed orders only."]
    assert body["explanation"] == "Revenue from completed orders placed last month."


def test_an_unknown_column_is_fixed_by_one_retry(client, model, executed):
    model["What is total revenue?"] = ["SELECT SUM(total_amont) AS revenue FROM orders WHERE status = 'completed'",
                                       "SELECT SUM(total_amount) AS revenue FROM orders WHERE status = 'completed'"]
    body = ask(client, "What is total revenue?").json()
    assert body["rows"][0]["revenue"] > 0 and body["sql"].startswith("SELECT SUM(total_amount)")
    assert len(executed) == 2 and len(model.calls) == 2
    feedback = model.calls[1][-1]["content"]  # PostgreSQL's own message and hint: names only, no row values
    assert 'column "total_amont" does not exist' in feedback
    assert 'Perhaps you meant to reference the column "orders.total_amount"' in feedback


@pytest.mark.parametrize("retry", ["SELECT rolname FROM pg_roles", "SELECT * FROM public.orders",
                                   "DELETE FROM orders", "SELECT pg_read_file('/etc/passwd')"])
def test_the_retry_is_checked_like_any_query(client, model, executed, retry):
    model["What is total revenue?"] = ["SELECT SUM(total_amont) FROM orders", retry]
    assert ask(client, "What is total revenue?").status_code == 400
    assert len(model.calls) == 2 and executed == ["SELECT SUM(total_amont) FROM orders LIMIT 101"]


def test_a_write_attempt_is_refused_and_never_retried(client, model, executed):
    model["Delete all cancelled orders."] = ["DELETE FROM orders WHERE status = 'cancelled'",
                                             "SELECT count(*) FROM orders WHERE status = 'cancelled'"]
    response = ask(client, "Delete all cancelled orders.")
    assert response.status_code == 400 and "read-only" in response.json()["detail"]
    assert len(model.calls) == 1 and executed == []


def test_a_reply_that_stays_malformed_is_a_clear_error(client, model, executed):
    model["How many orders are there?"] = [{"query": "SELECT count(*) FROM orders"},
                                           {"sql": "SELECT count(*) FROM orders"}]
    response = ask(client, "How many orders are there?")
    assert response.status_code == 502 and "not in the expected format" in response.json()["detail"]
    assert len(model.calls) == 2 and executed == []


def test_other_database_errors_are_reported_without_a_retry(client, model):
    model["What is the sum of the statuses?"] = ["SELECT SUM(status) AS total FROM orders", "SELECT 1"]
    response = ask(client, "What is the sum of the statuses?")
    assert response.status_code == 422 and "function or operator" in response.json()["detail"]
    assert len(model.calls) == 1


def test_a_retry_counts_against_the_daily_limit(client, model, monkeypatch):
    monkeypatch.setattr(generator, "_llm_quota", DailyQuota(1))
    model["What is total revenue?"] = ["SELECT SUM(total_amont) FROM orders", "SELECT SUM(total_amount) FROM orders"]
    response = ask(client, "What is total revenue?")
    assert response.status_code == 422 and "column that doesn't exist" in response.json()["detail"]
    assert len(model.calls) == 1  # today's only model call went to the first try
    assert ask(client, "How many orders are pending?").json()["generator"] == "rules"  # demo questions still work
