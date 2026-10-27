"""One-off database setup: tables, demo data, the read-only reader role and (optionally) the CSV ingest role.

Run with the owner credentials, never from the long-running API process:

    ADMIN_DATABASE_URL=... DATABASE_URL=... python -m app.setup_db

The reader's name and password are taken from DATABASE_URL, and the ingest role's from INGEST_DATABASE_URL (if
set), so there is one source of truth. Safe to run repeatedly.
"""
from psycopg import sql
from sqlalchemy.engine import make_url

from .config import settings
from .database import create_admin_engine, get_engine, verify_query_role
from .datasets import UPLOADS_SCHEMA, ensure_registry, verify_ingest_role
from .seed import initialize_database


def _configure_reader(admin_engine) -> str:
    reader = make_url(settings.database_url)
    admin = make_url(settings.admin_database_url)
    if not reader.username or not reader.password:
        raise RuntimeError("DATABASE_URL must include the reader role's username and password.")
    if reader.username == admin.username:
        raise RuntimeError("DATABASE_URL and ADMIN_DATABASE_URL must use different roles.")

    role = sql.Identifier(reader.username)
    connection = admin_engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (reader.username,))
        verb = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if cursor.fetchone() else "CREATE ROLE {} LOGIN PASSWORD {}"
        cursor.execute(sql.SQL(verb).format(role, sql.Literal(reader.password)))

        # Role-level defaults are enforced by PostgreSQL even if application code is bypassed.
        for setting, value in (("default_transaction_read_only", "on"),
                               ("statement_timeout", str(settings.statement_timeout_ms)),
                               ("idle_in_transaction_session_timeout", "10000"),
                               ("lock_timeout", "2000")):
            cursor.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(role, sql.Identifier(setting), sql.Literal(value)))

        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(connection.driver_connection.info.dbname), role))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role))
        for table in settings.exposed_tables:
            cursor.execute(sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(sql.Identifier("public", table), role))
        connection.commit()
    finally:
        connection.close()
    return reader.username


def _configure_ingest(admin_engine, reader_name: str) -> str | None:
    """The role that imports CSV uploads: it may only create and change tables in the uploads schema."""
    if not settings.ingest_database_url:
        return None
    ingest = make_url(settings.ingest_database_url)
    if not ingest.username or not ingest.password:
        raise RuntimeError("INGEST_DATABASE_URL must include the ingest role's username and password.")
    if ingest.username in (make_url(settings.admin_database_url).username, reader_name):
        raise RuntimeError("INGEST_DATABASE_URL must use its own role, not the owner or the reader.")

    role, schema = sql.Identifier(ingest.username), sql.Identifier(UPLOADS_SCHEMA)
    connection = admin_engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ingest.username,))
        verb = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if cursor.fetchone() else "CREATE ROLE {} LOGIN PASSWORD {}"
        cursor.execute(sql.SQL(verb).format(role, sql.Literal(ingest.password)))
        for setting, value in (("statement_timeout", "60000"), ("idle_in_transaction_session_timeout", "60000"),
                               ("lock_timeout", "5000")):
            cursor.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(role, sql.Identifier(setting), sql.Literal(value)))
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(connection.driver_connection.info.dbname), role))
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
        cursor.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(schema))
        cursor.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(schema, role))
        # The reader may look inside the schema; it only gets SELECT on each uploaded table as it is created.
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, sql.Identifier(reader_name)))
        connection.commit()
    finally:
        connection.close()
    ensure_registry()
    verify_ingest_role()
    return ingest.username


def main() -> None:
    admin_engine = create_admin_engine()
    initialize_database(admin_engine)
    reader = _configure_reader(admin_engine)
    verify_query_role(get_engine())
    print(f"Database ready. Reader role '{reader}' can read: {', '.join(settings.exposed_tables)}")
    ingest = _configure_ingest(admin_engine, reader)
    print(f"CSV uploads enabled. Ingest role '{ingest}' can only write to the '{UPLOADS_SCHEMA}' schema."
          if ingest else "CSV uploads disabled (INGEST_DATABASE_URL is not set).")


if __name__ == "__main__":
    main()
