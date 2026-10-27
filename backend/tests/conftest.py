"""Shared test setup.

Unit tests need no database. PostgreSQL tests run when both variables are set, against a database you can
throw away (setup creates tables, demo data and the reader role there):

    TEST_ADMIN_DATABASE_URL=postgresql+psycopg://<owner>:<password>@localhost:5432/datatalk_test
    TEST_DATABASE_URL=postgresql+psycopg://datatalk_test_reader:<password>@localhost:5432/datatalk_test
    TEST_INGEST_DATABASE_URL=postgresql+psycopg://datatalk_test_ingest:<password>@localhost:5432/datatalk_test

CSV-upload tests also need TEST_INGEST_DATABASE_URL.
"""
import os

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
