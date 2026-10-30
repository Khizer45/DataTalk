"""Shared test setup.

Unit tests need no database. PostgreSQL tests run when both variables are set, against a database you can
throw away (setup creates tables, demo data and the reader role there):

    TEST_ADMIN_DATABASE_URL=postgresql+psycopg://<owner>:<password>@localhost:5432/datatalk_test
    TEST_DATABASE_URL=postgresql+psycopg://datatalk_test_reader:<password>@localhost:5432/datatalk_test
    TEST_INGEST_DATABASE_URL=postgresql+psycopg://datatalk_test_ingest:<password>@localhost:5432/datatalk_test

CSV-upload tests also need TEST_INGEST_DATABASE_URL.
"""
import json
import os
import re

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.getenv("TEST_ADMIN_DATABASE_URL")
TEST_INGEST_DATABASE_URL = os.getenv("TEST_INGEST_DATABASE_URL")

# Settings are read when the app is imported, so configure the environment first.
if TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["ADMIN_DATABASE_URL"] = TEST_ADMIN_DATABASE_URL
    if TEST_INGEST_DATABASE_URL:
        os.environ["INGEST_DATABASE_URL"] = TEST_INGEST_DATABASE_URL
os.environ["LLM_API_KEY"] = ""  # routine tests never call a real model
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["UPLOAD_RATE_LIMIT_PER_MINUTE"] = "0"


@pytest.fixture(scope="session")
def database():
    if not (TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL):
        pytest.skip("set TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL to run PostgreSQL tests")
    from app.setup_db import main as run_setup
    run_setup()


@pytest.fixture
def client(database):
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def uploads(database):
    if not TEST_INGEST_DATABASE_URL:
        pytest.skip("set TEST_INGEST_DATABASE_URL to run CSV-upload tests")
    # Start each test with no datasets, so repeated local runs never reach the storage cap.
    from sqlalchemy import text

    from app.datasets import cleanup_expired, ensure_registry, get_ingest_engine
    ensure_registry()
    with get_ingest_engine().begin() as connection:
        connection.execute(text("UPDATE uploads.dataset_registry SET expires_at = now() - interval '1 second'"))
    cleanup_expired()


class StandInModel(dict):
    """The stand-in model's replies, by question. calls holds the messages of every call, in order."""

    def __init__(self):
        super().__init__()
        self.calls = []


@pytest.fixture
def model(monkeypatch):
    """AI mode with a stand-in model (no API key, no network). model[question] is what it replies:

    - SQL text: sent as {"sql": <the SQL>, "explanation": "Stand-in model", "assumptions": []};
    - a dict: sent as that JSON object (an answer, a clarification, or something malformed);
    - a list: one of the above per call, the second item answering the retry.

    {table} is replaced by the first table in the schema the model is shown. Everything after the model's reply
    (strict parsing, the retry, validation, isolation, read-only execution) is real. Each test gets a fresh daily quota.
    """
    from app import generator
    from app.limits import DailyQuota
    monkeypatch.setattr(generator.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(generator.settings, "llm_model", "test-model")
    monkeypatch.setattr(generator, "_llm_quota", DailyQuota(generator.settings.llm_daily_limit))
    answers = StandInModel()

    def chat(messages):
        answers.calls.append(messages)
        prompt = messages[1]["content"]
        reply = answers[prompt.partition("\n\nQuestion: ")[2]]
        if isinstance(reply, list):
            reply = reply[len(messages) // 2 - 1]  # 2 messages on the first call, 2 more on the retry
        if isinstance(reply, str):
            reply = {"sql": reply, "explanation": "Stand-in model", "assumptions": []}
        table = re.match(r"Schema:\n(\w+)\(", prompt).group(1)
        return json.dumps(reply).replace("{table}", table)

    monkeypatch.setattr(generator, "_chat", chat)
    return answers
