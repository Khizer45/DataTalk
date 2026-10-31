"""End-to-end checks against a real PostgreSQL database (skipped unless TEST_DATABASE_URL is set)."""
import json

import pytest
from sqlalchemy import text

from app import evaluation, main
from app.database import get_engine
from app.rules import SAMPLE_QUESTIONS
from app.limits import RateLimiter


def test_query_role_is_read_only_and_not_a_superuser(database):
    with get_engine().connect() as connection:
        row = connection.execute(text(
            "SELECT rolsuper, current_setting('default_transaction_read_only') AS read_only "
            "FROM pg_roles WHERE rolname = current_user")).one()
    assert row.rolsuper is False and row.read_only == "on"


def test_health_and_request_id(client):
    response = client.get("/api/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"]


def test_schema_describes_the_demo_dataset(client):
    body = client.get("/api/schema").json()
    counts = {table["name"]: table["row_count"] for table in body["tables"]}
    assert counts == {"customers": 250, "products": 13, "orders": 2431, "order_items": 3604}
    assert body["mode"] == "demo" and body["dataset"]["synthetic"] is True
    assert any(item.startswith("Revenue:") for item in body["dataset"]["definitions"])
    orders = next(table for table in body["tables"] if table["name"] == "orders")
    customer_id = next(column for column in orders["columns"] if column["name"] == "customer_id")
    assert customer_id["references"] == "customers.id" and orders["description"]


@pytest.mark.parametrize("question", SAMPLE_QUESTIONS)
def test_suggested_questions_work(client, question):
    response = client.post("/api/query", json={"question": question})
    assert response.status_code == 200 and response.json()["row_count"] > 0


def test_top_five_returns_five_rows(client):
    body = client.post("/api/query", json={"question": "Show the top 5 products by revenue"}).json()
    assert body["row_count"] == 5 and body["truncated"] is False


def test_unanswerable_question_is_declined(client):
    response = client.post("/api/query", json={"question": "Which customers live in Lahore?"})
    assert response.status_code == 422 and "never guesses" in response.json()["detail"]


def test_demo_mode_answers_composed_questions(client):
    response = client.post("/api/query", json={"question": "Which 3 countries generated the most revenue in 2025?"})
    body = response.json()
    assert response.status_code == 200 and body["row_count"] == 3
    assert "2025" in body["explanation"] and body["rows"][0]["revenue"] >= body["rows"][1]["revenue"]


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "_rate_limiter", RateLimiter(2))
    codes = [client.post("/api/query", json={"question": "How many orders are pending?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_every_evaluation_reference_query_runs(database):
    cases = json.loads((evaluation.EVAL_DIR / "cases.json").read_text())["cases"]
    allowed = [table["name"] for table in evaluation.get_tables()]
    for case in cases:
        for sql in [case.get("sql"), *case.get("alt_sql", [])]:
            if sql:
                assert evaluation._run_reference(sql, allowed), case["id"]


def test_scoring_gives_full_marks_to_the_reference_answers(database, model):
    # A stand-in model that replies with each reference query, through the same pipeline as the app.
    cases = [case for case in json.loads((evaluation.EVAL_DIR / "cases.json").read_text())["cases"]
             if case["expected"] == "answer"]
    model.update({case["question"]: case["sql"] for case in cases})
    summary = evaluation.run_evaluation(cases)["summary"]
    assert summary["execution_accuracy"] == 1.0 and summary["retry_rate"] == 0
