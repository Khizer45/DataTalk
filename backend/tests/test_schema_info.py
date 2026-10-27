import json

from app.database import Base
from app.evaluation import EVAL_DIR
from app.safety import validate_read_query
from app.schema_info import describe_schema, get_tables, load_business_context

import app.models  # noqa: F401  (registers the tables on Base.metadata)

TABLES = ["customers", "products", "orders", "order_items"]


def _normalize(text: str) -> str:
    return " ".join(text.casefold().replace(";", "").split())


def test_described_columns_exist_in_the_models():
    model_columns = {f"{t.name}.{c.name}" for t in Base.metadata.sorted_tables for c in t.columns}
    context = load_business_context()
    assert set(context["columns"]) <= model_columns
    assert set(context["tables"]) == set(TABLES)


def test_glossary_entries_are_complete():
    glossary = load_business_context()["glossary"]
    assert {"Revenue", "Average order value", "Cancellation rate"} <= {item["term"] for item in glossary}
    assert all(item["definition"].strip() for item in glossary)


def test_examples_are_valid_and_kept_out_of_the_evaluation_set():
    examples = load_business_context()["examples"]
    cases = json.loads((EVAL_DIR / "cases.json").read_text())["cases"]
    eval_questions = {_normalize(case["question"]) for case in cases}
    eval_sql = {_normalize(sql) for case in cases for sql in [case.get("sql"), *case.get("alt_sql", [])] if sql}
    for example in examples:
        validate_read_query(example["sql"], TABLES, 100)
        assert _normalize(example["question"]) not in eval_questions
        assert _normalize(example["sql"]) not in eval_sql


def test_tables_include_keys_and_references(database):
    tables = {t["name"]: {c["name"]: c for c in t["columns"]} for t in get_tables()}
    assert tables["customers"]["id"]["primary_key"]
    assert tables["orders"]["customer_id"]["references"] == "customers.id"
    assert tables["order_items"]["product_id"]["references"] == "products.id"
    assert tables["orders"]["shipped_date"]["nullable"] and not tables["orders"]["status"]["nullable"]


def test_basic_description_is_names_only(database):
    expected = "\n".join(f"{t['name']}({', '.join(c['name'] for c in t['columns'])})" for t in get_tables())
    assert describe_schema("basic") == expected


def test_full_description_adds_meaning_without_personal_data(database):
    text = describe_schema("full")
    assert "customer_id (integer, references customers.id)" in text
    assert "values: 'cancelled', 'completed', 'pending'" in text
    assert "from 2024-01-01 to 2025-12-31" in text
    assert "@" not in text  # no email addresses or other row data beyond the listed columns


def test_examples_run_on_the_reader_role(database):
    from app.database import execute_read_query
    for example in load_business_context()["examples"]:
        assert execute_read_query(validate_read_query(example["sql"], TABLES, 100)), example["question"]
