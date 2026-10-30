"""Databases the server operator connects DataTalk to, in addition to the demo data and CSV uploads.

Sources are configured only with environment variables on the server, never from the browser: accepting connection
strings from visitors would let a public deployment be used to reach internal networks, and would mean holding other
people's credentials. Each source must connect as a dedicated read-only role, which is checked before the source is
offered and again whenever its table list is refreshed. Questions are limited to the tables that role can read in
one schema, and run read-only with a time limit, like every other question.

    DATA_SOURCES=sales                   source ids, comma-separated (lowercase letters, digits and _)
    SOURCE_SALES_URL=postgresql+psycopg://datatalk_readonly:<password>@db.example.com:5432/sales?sslmode=require
    SOURCE_SALES_LABEL=Sales database    optional; shown in the app (default: the id)
    SOURCE_SALES_SCHEMA=public           optional; the one schema questions may use (default: public)
    SOURCE_SALES_TABLES=orders,products  optional allowlist (default: every table the role can read there)
    SOURCE_SALES_ACCESS_KEY=<long key>   optional; when set, the key must be sent in X-Source-Key
"""
import hmac
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .database import UnsafeDatabaseRoleError

logger = logging.getLogger("datatalk.sources")

_ID = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MAX_TABLES = 100
REFRESH_SECONDS = 300  # table list and role checks are redone this often
RETRY_SECONDS = 60     # a source that failed its checks or could not be reached is retried after this
# Roles that grant file access, program execution or writes everywhere; a source role must not be in any of them.
_DANGEROUS_ROLES = ("pg_read_server_files", "pg_write_server_files", "pg_execute_server_program", "pg_write_all_data")


class SourceNotFound(LookupError):
    """Unknown, unavailable, or failed its safety checks; the reason is logged, not shown."""


class SourceKeyRequired(PermissionError):
    pass


@dataclass
class Source:
    id: str
    label: str
    schema: str
    allowlist: list[str] | None
    url: str = field(repr=False)
    access_key: str = field(repr=False)
    _engine: Engine | None = field(default=None, repr=False)
    _tables: list[dict] | None = field(default=None, repr=False)
    _checked_at: float = 0.0

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self.url, pool_pre_ping=True, pool_size=2, max_overflow=2,
                                         connect_args={"connect_timeout": 5})
        return self._engine


_sources: dict[str, Source] | None = None
_lock = threading.Lock()


def _load() -> dict[str, Source]:
    sources = {}
    for source_id in settings.data_sources:
        if not _ID.match(source_id):
            logger.error("DATA_SOURCES entry %r is not a valid id (lowercase letters, digits, _); skipped",
                         source_id[:40])
            continue
        prefix = f"SOURCE_{source_id.upper()}_"
        url = os.getenv(prefix + "URL", "")
        schema = os.getenv(prefix + "SCHEMA", "public").strip()
        tables = [t.strip() for t in os.getenv(prefix + "TABLES", "").split(",") if t.strip()]
        if not url.startswith("postgresql"):
            logger.error("data source %s: %sURL must be a PostgreSQL URL; skipped", source_id, prefix)
            continue
        if not _IDENTIFIER.match(schema):
            logger.error("data source %s: %sSCHEMA is not a plain lowercase schema name; skipped", source_id, prefix)
            continue
        if any(not _IDENTIFIER.match(t) for t in tables):
            logger.error("data source %s: %sTABLES must be plain lowercase table names; skipped", source_id, prefix)
            continue
        sources[source_id] = Source(id=source_id, label=os.getenv(prefix + "LABEL", "").strip()[:60] or source_id,
                                    schema=schema, allowlist=tables or None, url=url,
                                    access_key=os.getenv(prefix + "ACCESS_KEY", ""))
        if not sources[source_id].access_key:
            logger.warning("data source %s has no access key: anyone who can open this DataTalk can query it",
                           source_id)
    return sources


def configured() -> dict[str, Source]:
    global _sources
    with _lock:
        if _sources is None:
            _sources = _load()
        return _sources


def reset() -> None:
    """Forget sources and their connections (tests, or after changing the environment)."""
    global _sources
    with _lock:
        for source in (_sources or {}).values():
            if source._engine is not None:
                source._engine.dispose()
        _sources = None


def _verify(source: Source, connection) -> None:
    """Refuse a role that could change data or reach the server, whatever the SQL checks would catch later."""
    row = connection.execute(text(
        "SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolreplication, "
        "current_setting('default_transaction_read_only') AS read_only, "
        "EXISTS (SELECT 1 FROM pg_roles g WHERE g.rolname = ANY(:dangerous) "
        "        AND pg_has_role(current_user, g.oid, 'MEMBER')) AS dangerous, "
        "EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema) AS schema_exists "
        "FROM pg_roles r WHERE r.rolname = current_user"), {"dangerous": list(_DANGEROUS_ROLES),
                                                           "schema": source.schema}).one()
    if row.rolsuper:
        raise UnsafeDatabaseRoleError("connects as a superuser")
    if row.rolcreaterole or row.rolcreatedb or row.rolreplication:
        raise UnsafeDatabaseRoleError("the role can create roles or databases, or replicate")
    if row.read_only != "on":
        raise UnsafeDatabaseRoleError("the role is not read-only by default (default_transaction_read_only)")
    if row.dangerous:
        raise UnsafeDatabaseRoleError("the role is a member of a file, program or write-all role")
    if not row.schema_exists:
        raise UnsafeDatabaseRoleError(f"schema {source.schema} does not exist")
    privileges = connection.execute(text(
        "SELECT has_schema_privilege(:schema, 'USAGE') AS usage, has_schema_privilege(:schema, 'CREATE') AS can_create, "
        "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema "
        "        AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "        AND has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')) AS can_write"),
        {"schema": source.schema}).one()
    if not privileges.usage:
        raise UnsafeDatabaseRoleError(f"the role cannot use schema {source.schema}")
    if privileges.can_create or privileges.can_write:
        raise UnsafeDatabaseRoleError(f"the role can create or change tables in schema {source.schema}")


def _read_tables(source: Source, connection) -> list[dict]:
    rows = connection.execute(text(
        "SELECT c.relname AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
        "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND has_table_privilege(c.oid, 'SELECT') ORDER BY c.relname, a.attnum"), {"schema": source.schema}).all()
    tables: dict[str, list[dict]] = {}
    for table_name, column_name, column_type in rows:
        # Only plain lowercase table names: they need no quoting, so the table checks compare them exactly.
        if not _IDENTIFIER.match(table_name) or (source.allowlist and table_name not in source.allowlist):
            continue
        if table_name not in tables and len(tables) >= MAX_TABLES:
            continue
        tables.setdefault(table_name, []).append({"name": column_name, "type": column_type})
    return [{"name": name, "columns": columns} for name, columns in tables.items()]


def tables_of(source: Source) -> list[dict] | None:
    """The source's readable tables, or None while it is unreachable or fails its checks."""
    now = time.monotonic()
    fresh = REFRESH_SECONDS if source._tables is not None else RETRY_SECONDS
    if source._checked_at and now - source._checked_at < fresh:
        return source._tables
    source._checked_at = now
    try:
        with source.engine.connect() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout = 5000")
            _verify(source, connection)
            tables = _read_tables(source, connection)
        if not tables:
            raise UnsafeDatabaseRoleError(f"the role cannot read any table in schema {source.schema}")
        if source._tables is None:
            logger.info("data source %s ready: %d tables", source.id, len(tables))
        source._tables = tables
    except UnsafeDatabaseRoleError as exc:
        logger.error("data source %s refused: %s", source.id, exc)
        source._tables = None
    except SQLAlchemyError as exc:
        logger.warning("data source %s unavailable: %s", source.id, type(exc).__name__)
        source._tables = None
    return source._tables


def available() -> list[Source]:
    return [source for source in configured().values() if tables_of(source)]


def require(source_id: str, key: str) -> tuple[Source, list[dict]]:
    source = configured().get(source_id)
    tables = tables_of(source) if source else None
    if not tables:
        raise SourceNotFound()
    if source.access_key and not hmac.compare_digest(key.encode(), source.access_key.encode()):
        raise SourceKeyRequired()
    return source, tables


def public_info(source: Source, tables: list[dict], unlocked: bool = False) -> dict:
    """What the app shows about a source. A source with an access key hides its tables until the key is given."""
    info = {"id": source.id, "label": source.label, "needs_key": bool(source.access_key)}
    if unlocked or not source.access_key:
        info["tables"] = tables
    return info


def schema_text(tables: list[dict]) -> str:
    """The same names-only form the demo tables are described in."""
    def quoted(name: str) -> str:
        return name if _IDENTIFIER.match(name) else '"' + name.replace('"', '""') + '"'
    return "\n".join(f"{t['name']}({', '.join(quoted(c['name']) for c in t['columns'])})" for t in tables)
