"""What DataTalk knows about the exposed tables.

Structure (types, keys, relationships) comes from PostgreSQL; meaning (descriptions, glossary, examples) comes
from business_context.json. Both are cached for a few minutes because they rarely change.
"""
import json
import threading
import time
from functools import lru_cache
from pathlib import Path

from sqlalchemy import column, func, inspect, select, table

from .config import settings
from .database import get_engine

CACHE_SECONDS = 300
MAX_LISTED_VALUES = 12
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: str, build):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = build()
    with _lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()
    load_business_context.cache_clear()


@lru_cache(maxsize=1)
def load_business_context() -> dict:
    path = Path(settings.business_context_path) if settings.business_context_path \
        else Path(__file__).with_name("business_context.json")
    return json.loads(path.read_text()) if path.exists() else {}


def get_tables() -> list[dict]:
    """Exposed tables with their columns, keys, references and descriptions."""
    return _cached("tables", _read_tables)


def _read_tables() -> list[dict]:
    inspector = inspect(get_engine())
    existing = set(inspector.get_table_names())
    context = load_business_context()
    described = context.get("columns", {})
    tables = []
    for name in settings.exposed_tables:
        if name not in existing:
            continue
        primary = set(inspector.get_pk_constraint(name).get("constrained_columns") or [])
        references = {local: f"{fk['referred_table']}.{remote}" for fk in inspector.get_foreign_keys(name)
                      for local, remote in zip(fk["constrained_columns"], fk["referred_columns"])}
        columns = [{"name": c["name"], "type": str(c["type"]), "nullable": bool(c["nullable"]),
                    "primary_key": c["name"] in primary, "references": references.get(c["name"]),
                    "description": described.get(f"{name}.{c['name']}", {}).get("description")}
                   for c in inspector.get_columns(name)]
        tables.append({"name": name, "description": context.get("tables", {}).get(name), "columns": columns})
    return tables


def get_table_row_counts(table_names: list[str]) -> dict[str, int]:
    def count() -> dict[str, int]:
        with get_engine().connect() as connection:
            return {name: connection.execute(select(func.count()).select_from(table(name))).scalar_one()
                    for name in table_names}
    return _cached(f"row_counts:{','.join(table_names)}", count)


def _literal(value) -> str:
    return "'" + str(value).replace("'", "''") + "'" if isinstance(value, str) else str(value)


def _column_facts(tables: list[dict]) -> dict[str, str]:
    """Value lists and date ranges for the columns business_context.json marks as safe to share with the model."""
    marked = load_business_context().get("columns", {})
    facts = {}
    with get_engine().connect() as connection:
        for t in tables:
            for c in t["columns"]:
                key = f"{t['name']}.{c['name']}"
                options = marked.get(key, {})
                target = table(t["name"], column(c["name"])).c[c["name"]]
                if options.get("list_values"):
                    values = connection.execute(select(target).distinct().order_by(target)
                                                .limit(MAX_LISTED_VALUES + 1)).scalars().all()
                    if len(values) <= MAX_LISTED_VALUES and all(len(str(v)) <= 60 for v in values):
                        facts[key] = "values: " + ", ".join(_literal(v) for v in values if v is not None)
                if options.get("show_range"):
                    low, high = connection.execute(select(func.min(target), func.max(target))).one()
                    facts[key] = f"from {low} to {high}"
    return facts


def describe_schema(variant: str = "full") -> str:
    """Schema text for the model. 'basic' is names only (for comparisons); 'full' adds types, keys and meaning."""
    tables = get_tables()
    if variant == "basic":
        return "\n".join(f"{t['name']}({', '.join(c['name'] for c in t['columns'])})" for t in tables)
    facts = _cached("facts", lambda: _column_facts(tables))
    lines = []
    for t in tables:
        lines.append(f"TABLE {t['name']}" + (f" -- {t['description']}" if t["description"] else ""))
        for c in t["columns"]:
            parts = [c["type"].lower()]
            if c["primary_key"]:
                parts.append("primary key")
            if c["references"]:
                parts.append(f"references {c['references']}")
            if c["nullable"]:
                parts.append("can be NULL")
            detail = " ".join(x for x in (c["description"], facts.get(f"{t['name']}.{c['name']}")) if x)
            lines.append(f"  {c['name']} ({', '.join(parts)})" + (f" -- {detail}" if detail else ""))
    return "\n".join(lines)


def get_definitions() -> list[dict]:
    return [{"term": g["term"], "definition": g["definition"]} for g in load_business_context().get("glossary", [])]
