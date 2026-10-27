"""Uploaded datasets: one isolated table each, reachable only with its secret token, removed when it expires.

All writes go through the ingest role, which can only create and change tables in the uploads schema. Questions
still run through the read-only reader role, which is granted SELECT on each uploaded table when it is created.

Ownership without user accounts: an upload returns a public dataset id and a secret access token. Only a SHA-256
hash of the token is stored, and every read checks it. This is capability-based access for temporary datasets,
not user authentication.
"""
import hashlib
import hmac
import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

from .config import settings
from .csv_import import SQL_TYPES, ParsedCsv
from .database import UnsafeDatabaseRoleError

UPLOADS_SCHEMA = "uploads"
_REGISTRY = sql.Identifier(UPLOADS_SCHEMA, "dataset_registry")
_DATASET_ID = re.compile(r"^[A-Za-z0-9_-]{16}$")
_TABLE_NAME = re.compile(r"^t_[0-9a-f]{20}$")


class DatasetNotFound(LookupError):
    """Unknown id, wrong token or expired; deliberately indistinguishable so ids cannot be probed."""


class UploadsFull(RuntimeError):
    pass


def uploads_enabled() -> bool:
    return bool(settings.ingest_database_url)


_engine: Engine | None = None


def get_ingest_engine() -> Engine:
    global _engine
    if _engine is None:
        if not settings.ingest_database_url.startswith("postgresql"):
            raise RuntimeError("INGEST_DATABASE_URL must be a PostgreSQL URL.")
        _engine = create_engine(settings.ingest_database_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _engine


@contextmanager
def _cursor():
    connection = get_ingest_engine().raw_connection()
    try:
        yield connection, connection.cursor()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_registry() -> None:
    with _cursor() as (connection, cursor):
        cursor.execute(sql.SQL(
            "CREATE TABLE IF NOT EXISTS {} (id text PRIMARY KEY, table_name text NOT NULL UNIQUE, "
            "display_name text NOT NULL, columns jsonb NOT NULL, row_count integer NOT NULL, "
            "token_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), "
            "expires_at timestamptz NOT NULL)").format(_REGISTRY))
        connection.commit()


def verify_ingest_role() -> None:
    """Refuse uploads if the ingest role is a superuser or could read the demo tables."""
    with _cursor() as (_, cursor):
        cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        if cursor.fetchone()[0]:
            raise UnsafeDatabaseRoleError("INGEST_DATABASE_URL connects as a superuser. Use the ingest role.")
        for table in settings.exposed_tables:
            cursor.execute("SELECT has_table_privilege(c, 'SELECT') FROM to_regclass(%s) AS c WHERE c IS NOT NULL",
                           (f"public.{table}",))
            row = cursor.fetchone()
            if row and row[0]:
                raise UnsafeDatabaseRoleError("INGEST_DATABASE_URL can read the demo tables. Use the ingest role.")


def _public(dataset_id: str, display_name: str, columns: list[dict], row_count: int, expires_at: datetime) -> dict:
    return {"id": dataset_id, "name": display_name, "columns": columns, "row_count": row_count,
            "expires_at": expires_at.isoformat()}


def create_dataset(parsed: ParsedCsv, display_name: str) -> dict:
    """Import the rows into a new table and register it in one transaction; a failure leaves nothing behind."""
    reader = make_url(settings.database_url).username
    dataset_id = secrets.token_urlsafe(12)
    token = secrets.token_urlsafe(32)
    table_name = f"t_{secrets.token_hex(10)}"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.dataset_ttl_hours)
    columns = [{"name": c.name, "label": c.label, "type": c.type, "nullable": c.nullable} for c in parsed.columns]
    table = sql.Identifier(UPLOADS_SCHEMA, table_name)
    definitions = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c.name), sql.SQL(SQL_TYPES[c.type]))
                                     for c in parsed.columns)
    names = sql.SQL(", ").join(sql.Identifier(c.name) for c in parsed.columns)
    with _cursor() as (connection, cursor):
        cursor.execute(sql.SQL("SELECT count(*) FROM {} WHERE expires_at > now()").format(_REGISTRY))
        if cursor.fetchone()[0] >= settings.max_active_datasets:
            raise UploadsFull("Upload storage for this demo is full right now. Please try again later.")
        cursor.execute(sql.SQL("CREATE TABLE {} ({})").format(table, definitions))
        with cursor.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(table, names)) as copy:
            for row in parsed.rows:
                copy.write_row(row)
        cursor.execute(sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(table, sql.Identifier(reader)))
        cursor.execute(sql.SQL("INSERT INTO {} (id, table_name, display_name, columns, row_count, token_hash, "
                               "expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)").format(_REGISTRY),
                       (dataset_id, table_name, display_name, json.dumps(columns), len(parsed.rows),
                        _token_hash(token), expires_at))
        connection.commit()
    return {**_public(dataset_id, display_name, columns, len(parsed.rows), expires_at), "access_token": token}


def get_dataset(dataset_id: str, token: str) -> dict:
    """The dataset's metadata, including its internal table name, if the token matches and it has not expired."""
    if not (dataset_id and token and _DATASET_ID.match(dataset_id)):
        raise DatasetNotFound()
    with _cursor() as (_, cursor):
        cursor.execute(sql.SQL("SELECT table_name, display_name, columns, row_count, token_hash, expires_at "
                               "FROM {} WHERE id = %s AND expires_at > now()").format(_REGISTRY), (dataset_id,))
        row = cursor.fetchone()
    if not row or not hmac.compare_digest(row[4], _token_hash(token)):
        raise DatasetNotFound()
    table_name, display_name, columns, row_count, _, expires_at = row
    return {**_public(dataset_id, display_name, columns, row_count, expires_at), "table_name": table_name}


def _drop(dataset_id: str, table_name: str) -> None:
    if not _TABLE_NAME.match(table_name):
        raise ValueError("Unexpected table name in the dataset registry.")
    with _cursor() as (connection, cursor):
        cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(UPLOADS_SCHEMA, table_name)))
        cursor.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(_REGISTRY), (dataset_id,))
        connection.commit()


def delete_dataset(dataset_id: str, token: str) -> None:
    _drop(dataset_id, get_dataset(dataset_id, token)["table_name"])


def cleanup_expired() -> int:
    with _cursor() as (_, cursor):
        cursor.execute(sql.SQL("SELECT id, table_name FROM {} WHERE expires_at <= now()").format(_REGISTRY))
        expired = cursor.fetchall()
    for dataset_id, table_name in expired:
        _drop(dataset_id, table_name)
    return len(expired)


def schema_text(dataset: dict) -> str:
    """The dataset in the same names-only form the demo tables are described to the model."""
    return f"{dataset['table_name']}({', '.join(column['name'] for column in dataset['columns'])})"
