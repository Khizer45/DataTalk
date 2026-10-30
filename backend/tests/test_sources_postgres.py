"""Databases configured on the server: role checks, table discovery, isolation, access keys.

A schema in the test database plays the operator's own database, reached through dedicated roles. Questions use the
stand-in model from conftest; validation, search_path and read-only execution are the real pipeline.
"""
import logging
import secrets

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import main, sources
from tests.conftest import TEST_ADMIN_DATABASE_URL

KEY = secrets.token_urlsafe(24)
ROLES = {
    # name: (default read-only, privileges on ext_support.tickets)
    "datatalk_test_source": (True, "SELECT"),
    "datatalk_test_source_rw": (True, "SELECT, INSERT"),
    "datatalk_test_source_lax": (False, "SELECT"),
}


def role_url(role: str, password: str) -> str:
    return make_url(TEST_ADMIN_DATABASE_URL).set(username=role, password=password).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def operator_database(database):
    """Tables and roles as an operator would set them up; passwords are random for each run."""
    password = secrets.token_hex(16)
    engine = create_engine(TEST_ADMIN_DATABASE_URL)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS ext_support CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA ext_support")
        connection.exec_driver_sql("CREATE TABLE ext_support.tickets (id integer PRIMARY KEY, status text, "
                                   "opened_on date)")
        connection.exec_driver_sql("INSERT INTO ext_support.tickets VALUES (1, 'open', '2025-05-01'), "
                                   "(2, 'closed', '2025-05-02'), (3, 'open', '2025-05-03')")
        connection.exec_driver_sql("CREATE TABLE ext_support.staff_notes (note text)")
        connection.exec_driver_sql("INSERT INTO ext_support.staff_notes VALUES ('private')")
        database_name = connection.exec_driver_sql("SELECT current_database()").scalar()
        for role, (read_only, privileges) in ROLES.items():
            connection.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') "
                                       f"THEN CREATE ROLE {role} LOGIN; END IF; END $$")
            connection.exec_driver_sql(f"ALTER ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                       f"PASSWORD '{password}'")
            connection.exec_driver_sql(f"ALTER ROLE {role} SET default_transaction_read_only = "
                                       f"{'on' if read_only else 'off'}")
            connection.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{database_name}" TO {role}')
            connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA ext_support TO {role}")
            connection.exec_driver_sql(f"GRANT {privileges} ON ext_support.tickets TO {role}")
    engine.dispose()
    return password


@pytest.fixture
def configured(operator_database, monkeypatch):
    def configure(**entries):
        sources.reset()
        monkeypatch.setattr(sources.settings, "data_sources", list(entries))
        for source_id, values in entries.items():
            for key, value in values.items():
                monkeypatch.setenv(f"SOURCE_{source_id.upper()}_{key}", value)
    yield configure
    sources.reset()


@pytest.fixture
def support(configured, operator_database):
    configured(support={"URL": role_url("datatalk_test_source", operator_database), "SCHEMA": "ext_support",
                        "LABEL": "Support desk"})


def ask(client, question, source="support", key=None):
    headers = {"X-Source-Key": key} if key is not None else {}
    return client.post("/api/query", json={"question": question, "source_id": source}, headers=headers)


def test_schema_lists_the_tables_the_role_can_read(client, support):
    listed = client.get("/api/schema").json()["sources"]
    assert listed == [{"id": "support", "label": "Support desk", "needs_key": False, "tables": [
        {"name": "tickets", "columns": [{"name": "id", "type": "integer"}, {"name": "status", "type": "text"},
                                        {"name": "opened_on", "type": "date"}]}]}]  # staff_notes is not granted


def test_questions_are_answered_from_the_connected_database(client, support, model):
    model["Tickets by status?"] = "SELECT status, count(*) AS n FROM {table} GROUP BY status ORDER BY status"
    body = ask(client, "Tickets by status?").json()
    assert body["rows"] == [{"status": "closed", "n": 1}, {"status": "open", "n": 2}]
    assert body["source"] == "database" and body["dataset_name"] == "Support desk" and body["generator"] == "llm"


def test_questions_about_databases_need_ai_mode(client, support):
    response = ask(client, "How many tickets are open?")
    assert response.status_code == 422 and "AI mode" in response.json()["detail"]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM staff_notes",               # in the schema, but not readable by the role
    "SELECT * FROM ext_support.staff_notes",   # schema-qualified
    "SELECT count(*) FROM orders",             # demo tables
    "SELECT count(*) FROM public.orders",
    "SELECT * FROM pg_roles",                  # catalog tables stay reachable by name, so the allowlist matters
    "DELETE FROM tickets",
    "SELECT pg_read_file('/etc/passwd')",
])
def test_a_source_cannot_reach_anything_else(client, support, model, sql):
    model["Try it"] = sql
    assert ask(client, "Try it").status_code == 400


def test_allowlist_limits_the_tables(client, configured, operator_database, model):
    configured(support={"URL": role_url("datatalk_test_source", operator_database), "SCHEMA": "ext_support",
                        "TABLES": "staff_notes"})
    # The only allowlisted table is not readable by the role, so nothing is left and the source is not offered.
    assert client.get("/api/schema").json()["sources"] == []
    assert ask(client, "Anything?").status_code == 404


def test_unsafe_roles_are_refused(client, configured, operator_database, caplog):
    caplog.set_level(logging.ERROR, logger="datatalk.sources")
    configured(
        writer={"URL": role_url("datatalk_test_source_rw", operator_database), "SCHEMA": "ext_support"},
        lax={"URL": role_url("datatalk_test_source_lax", operator_database), "SCHEMA": "ext_support"},
        owner={"URL": TEST_ADMIN_DATABASE_URL, "SCHEMA": "ext_support"},
        missing={"URL": role_url("datatalk_test_source", operator_database), "SCHEMA": "no_such_schema"},
    )
    assert client.get("/api/schema").json()["sources"] == []
    log = caplog.text
    assert "writer refused: the role can create or change tables" in log
    assert "lax refused: the role is not read-only by default" in log
    assert "owner refused: connects as a superuser" in log
    assert "missing refused: schema no_such_schema does not exist" in log
    assert operator_database not in log  # passwords never reach the log
    for source_id in ("writer", "lax", "owner", "missing", "nope"):
        assert ask(client, "Anything?", source=source_id).status_code == 404


def test_bad_configuration_is_skipped(configured, operator_database, caplog):
    configured(**{"bad": {"URL": "mysql://x"}, "weird": {"URL": role_url("datatalk_test_source", operator_database),
                                                         "SCHEMA": "Ext; DROP"}})
    assert sources.configured() == {}
    assert "must be a PostgreSQL URL" in caplog.text and "plain lowercase schema name" in caplog.text


def test_access_key(client, configured, operator_database, model):
    configured(support={"URL": role_url("datatalk_test_source", operator_database), "SCHEMA": "ext_support",
                        "ACCESS_KEY": KEY})
    listed = client.get("/api/schema").json()["sources"]
    assert listed == [{"id": "support", "label": "support", "needs_key": True}]  # tables hidden until unlocked
    model["Count"] = "SELECT count(*) AS n FROM {table}"
    assert ask(client, "Count").status_code == 403
    assert ask(client, "Count", key="wrong").status_code == 403
    assert ask(client, "Count", key=KEY).json()["rows"] == [{"n": 3}]
    assert client.get("/api/sources/support", headers={"X-Source-Key": "wrong"}).status_code == 403
    unlocked = client.get("/api/sources/support", headers={"X-Source-Key": KEY}).json()
    assert [t["name"] for t in unlocked["tables"]] == ["tickets"]


def test_one_source_at_a_time(client, support):
    response = client.post("/api/query", json={"question": "Anything?", "source_id": "support",
                                               "dataset_id": "A" * 16})
    assert response.status_code == 422
    assert client.post("/api/query", json={"question": "Anything?", "source_id": "Bad-Id"}).status_code == 422


def test_demo_still_works_with_sources_configured(client, support):
    body = client.post("/api/query", json={"question": "How many orders are pending?"}).json()
    assert body["source"] == "demo" and body["rows"]
    assert main.settings.data_sources == ["support"]
