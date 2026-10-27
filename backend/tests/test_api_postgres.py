"""End-to-end checks against a real PostgreSQL database (skipped unless TEST_DATABASE_URL is set)."""
import json

import pytest
from sqlalchemy import text

from app import evaluation, main
from app.database import get_engine
from app.generator import SAMPLE_QUESTIONS
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


@pytest.mark.parametrize("question", SAMPLE_QUESTIONS)
def test_suggested_questions_work(client, question):
    response = client.post("/api/query", json={"question": question})
    assert response.status_code == 200 and response.json()["row_count"] > 0


def test_top_five_returns_five_rows(client):
    body = client.post("/api/query", json={"question": "Show the top 5 products by revenue"}).json()
    assert body["row_count"] == 5 and body["truncated"] is False


def test_unanswerable_question_is_declined(client):
    response = client.post("/api/query", json={"question": "How many customers are from Pakistan?"})
    assert response.status_code == 422 and "never guesses" in response.json()["detail"]


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "_rate_limiter", RateLimiter(2))
    codes = [client.post("/api/query", json={"question": "How many orders are pending?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_every_evaluation_reference_query_runs(database):
    cases = json.loads((evaluation.EVAL_DIR / "cases.json").read_text())["cases"]
    tables, _ = evaluation.get_schema_context()
    allowed = [table["name"] for table in tables]
    for case in cases:
        for sql in [case.get("sql"), *case.get("alt_sql", [])]:
            if sql:
                assert evaluation._run_reference(sql, allowed), case["id"]


def test_scoring_gives_full_marks_to_the_reference_answers(database, monkeypatch):
    cases = [case for case in json.loads((evaluation.EVAL_DIR / "cases.json").read_text())["cases"]
             if case["expected"] == "answer"]
    by_question = {case["question"]: case["sql"] for case in cases}
    monkeypatch.setattr(evaluation, "generate_sql", lambda question, schema: (by_question[question], "reference", ""))
    assert evaluation.run_evaluation(cases)["summary"]["execution_accuracy"] == 1.0
