import re
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


class UnsafeDatabaseRoleError(RuntimeError):
    pass


class DatabaseBusyError(RuntimeError):
    pass


_query_slots = threading.BoundedSemaphore(settings.max_concurrent_queries)
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _require_postgres(url: str, name: str) -> str:
    if not url:
        raise RuntimeError(f"{name} is not set. See .env.example.")
    if not url.startswith("postgresql"):
        raise RuntimeError(f"{name} must be a PostgreSQL URL; DataTalk supports PostgreSQL only.")
    return url


_engine: Engine | None = None


def get_engine() -> Engine:
    """Engine for the read-only query role. Created lazily so tooling can import the app without a database."""
    global _engine
    if _engine is None:
        _engine = create_engine(_require_postgres(settings.database_url, "DATABASE_URL"), pool_pre_ping=True,
                                pool_size=settings.max_concurrent_queries, max_overflow=2)
    return _engine


def create_admin_engine() -> Engine:
    return create_engine(_require_postgres(settings.admin_database_url, "ADMIN_DATABASE_URL"))


def execute_read_query(sql: str, search_path: str | None = None, engine: Engine | None = None) -> list[dict]:
    """Run validated SQL in a read-only, time-limited transaction that is always rolled back.

    search_path pins name lookup to one schema (uploads, or a connected source's schema), so other tables cannot be
    reached by name. engine is a connected source's; the default is the demo/uploads reader.
    """
    if search_path is not None and not _SCHEMA_NAME.match(search_path):
        raise ValueError("Invalid search_path.")
    if not _query_slots.acquire(timeout=2):
        raise DatabaseBusyError("DataTalk is busy right now. Please try again in a moment.")
    try:
        with (engine or get_engine()).connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(settings.statement_timeout_ms)}")
            if search_path is not None:
                connection.exec_driver_sql(f"SET LOCAL search_path TO {search_path}")
            result = connection.execute(text(sql))
            return [dict(row) for row in result.mappings().all()]
    finally:
        _query_slots.release()


def verify_query_role(engine: Engine) -> None:
    """Refuse to serve queries through a role that is a superuser or can write by default."""
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT r.rolsuper, current_setting('default_transaction_read_only') AS read_only "
            "FROM pg_roles r WHERE r.rolname = current_user"
        )).one()
    if row.rolsuper:
        raise UnsafeDatabaseRoleError("DATABASE_URL connects as a superuser. Use the read-only reader role.")
    if row.read_only != "on":
        raise UnsafeDatabaseRoleError(
            "DATABASE_URL role is not read-only by default. Run `python -m app.setup_db` and use the reader role."
        )
