"""One-off database setup: tables, demo data and the read-only reader role.

Run with the owner credentials, never from the long-running API process:

    ADMIN_DATABASE_URL=... DATABASE_URL=... python -m app.setup_db

The reader's name and password are taken from DATABASE_URL so there is one source of truth.
Safe to run repeatedly.
"""
from psycopg import sql
from sqlalchemy.engine import make_url

from .config import settings
from .database import create_admin_engine, get_engine, verify_query_role
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


def main() -> None:
    admin_engine = create_admin_engine()
    initialize_database(admin_engine)
    reader = _configure_reader(admin_engine)
    verify_query_role(get_engine())
    print(f"Database ready. Reader role '{reader}' can read: {', '.join(settings.exposed_tables)}")


if __name__ == "__main__":
    main()
