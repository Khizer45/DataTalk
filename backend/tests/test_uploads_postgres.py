"""CSV upload end to end against PostgreSQL: import, ownership, isolation, querying, cleanup.

Questions about uploads need AI mode, so a stand-in model returns SQL for the table it is shown. Everything after
the model (validation, search_path isolation, read-only execution) is the real pipeline.
"""
import json
import re

import pytest
from sqlalchemy import text

from app import main
from app.datasets import get_ingest_engine
from app.limits import RateLimiter

SALES = b"Region,Units Sold,Closed On\nNorth,12,2025-01-05\nSouth,7,2025-01-06\nNorth,3,\nEast,10,2025-02-01\n"
EXPENSES = (b"date;category;amount;note\n2025-03-01;Rent;1200.00;March\n2025-03-02;Food;45.50;\n"
            b"2025-03-09;Food;30.25;groceries\n2025-04-01;Rent;1200.00;April\n")


def upload(client, data, name="data.csv"):
    return client.post(f"/api/datasets?name={name}", content=data, headers={"Content-Type": "text/csv"})


def ask(client, dataset, question, token=None):
    return client.post("/api/query", json={"question": question, "dataset_id": dataset["id"]},
                       headers={"X-Dataset-Token": dataset["access_token"] if token is None else token})


def test_upload_returns_inferred_schema_and_a_token(client, uploads):
    response = upload(client, SALES, "sales 2025.csv")
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "sales 2025.csv" and body["row_count"] == 4 and len(body["access_token"]) >= 40
    assert [(c["name"], c["label"], c["type"]) for c in body["columns"]] == [
        ("region", "Region", "text"), ("units_sold", "Units Sold", "integer"), ("closed_on", "Closed On", "date")]
    assert "table_name" not in body


def test_dataset_details_need_the_token(client, uploads):
    dataset = upload(client, SALES).json()
    headers = {"X-Dataset-Token": dataset["access_token"]}
    assert client.get(f"/api/datasets/{dataset['id']}", headers=headers).json()["row_count"] == 4
    assert client.get(f"/api/datasets/{dataset['id']}", headers={"X-Dataset-Token": "wrong"}).status_code == 404
    assert client.get(f"/api/datasets/{dataset['id']}").status_code == 404


def test_questions_about_uploads_need_ai_mode(client, uploads):
    dataset = upload(client, SALES).json()
    response = ask(client, dataset, "How many units were sold?")
    assert response.status_code == 422 and "AI mode" in response.json()["detail"]


def test_two_different_schemas_answer_from_their_own_data(client, uploads, model):
    sales = upload(client, SALES).json()
    expenses = upload(client, EXPENSES, "expenses.csv").json()
    assert [c["type"] for c in expenses["columns"]] == ["date", "text", "decimal", "text"]
    model["Units by region?"] = "SELECT region, SUM(units_sold) AS units FROM {table} GROUP BY region ORDER BY region"
    model["Food spending?"] = "SELECT SUM(amount) AS food FROM {table} WHERE category = 'Food'"

    body = ask(client, sales, "Units by region?").json()
    assert body["rows"] == [{"region": "East", "units": 10}, {"region": "North", "units": 15},
                            {"region": "South", "units": 7}]
    assert body["source"] == "upload" and body["generator"] == "llm" and body["dataset_name"] == "data.csv"
    assert ask(client, expenses, "Food spending?").json()["rows"] == [{"food": 75.75}]


def test_a_dataset_cannot_reach_other_data(client, uploads, model):
    first = upload(client, SALES).json()
    second = upload(client, EXPENSES).json()
    second_table = re.search(r"FROM (t_\w+)", ask_sql(client, second, model)).group(1)
    model["Other dataset?"] = "SELECT * FROM " + second_table
    model["Demo orders?"] = "SELECT count(*) FROM orders"
    model["Demo orders qualified?"] = "SELECT count(*) FROM public.orders"
    model["Other dataset via CTE?"] = f"WITH {second_table} AS (SELECT * FROM {second_table}) SELECT * FROM {second_table}"
    model["Catalog via CTE?"] = "WITH pg_class AS (SELECT * FROM pg_class) SELECT relname FROM pg_class"
    for question in ("Other dataset?", "Demo orders?", "Demo orders qualified?", "Other dataset via CTE?",
                     "Catalog via CTE?"):
        assert ask(client, first, question).status_code == 400, question
    assert ask(client, second, "Other dataset?", token=first["access_token"]).status_code == 404


def ask_sql(client, dataset, model):
    model["Everything?"] = "SELECT * FROM {table}"
    return ask(client, dataset, "Everything?").json()["sql"]


def test_a_retry_stays_inside_the_dataset(client, uploads, model):
    first = upload(client, SALES).json()
    second = upload(client, EXPENSES).json()
    second_table = re.search(r"FROM (t_\w+)", ask_sql(client, second, model)).group(1)
    model["Units in total?"] = ["SELECT SUM(units_sold) AS units FROM orders",
                                "SELECT SUM(units_sold) AS units FROM {table}"]
    assert ask(client, first, "Units in total?").json()["rows"] == [{"units": 32}]
    assert "outside the exposed schema: orders" in model.calls[-1][-1]["content"]
    # The retry goes through the same checks: reaching another upload on the second try is still refused.
    model["Units again?"] = ["SELECT SUM(units) FROM {table}", f"SELECT * FROM {second_table}"]
    response = ask(client, first, "Units again?")
    assert response.status_code == 400 and "outside the exposed schema" in response.json()["detail"]


def test_writes_are_rejected_for_uploads(client, uploads, model):
    dataset = upload(client, SALES).json()
    model["Delete it"] = "DELETE FROM {table}"
    assert ask(client, dataset, "Delete it").status_code == 400
    model["Count"] = "SELECT count(*) AS n FROM {table}"
    assert ask(client, dataset, "Count").json()["rows"] == [{"n": 4}]


def test_hostile_csv_content_is_stored_as_data(client, uploads, model):
    data = b'"name""); DROP TABLE uploads.dataset_registry; --",note\n"Robert\'); DROP TABLE x;--",=HYPERLINK(1)\n'
    dataset = upload(client, data).json()
    model["All rows"] = "SELECT * FROM {table}"
    rows = ask(client, dataset, "All rows").json()["rows"]
    assert rows == [{"name_drop_table_uploads_dataset_registry": "Robert'); DROP TABLE x;--", "note": "=HYPERLINK(1)"}]
    with get_ingest_engine().connect() as connection:
        assert connection.execute(text("SELECT to_regclass('uploads.dataset_registry') IS NOT NULL")).scalar()


@pytest.mark.parametrize("data, name, status, message", [
    (b"a,b\n1,2\n", "report.xlsx", 400, ".csv"),
    (b"", "empty.csv", 400, "empty"),
    (b"a,b\n1,2,3\n", "ragged.csv", 400, "header has 2"),
    (b"a,a\n1,2\n", "dupes.csv", 400, "unique"),
])
def test_bad_uploads_are_rejected_clearly(client, uploads, data, name, status, message):
    response = upload(client, data, name)
    assert response.status_code == status and message in response.json()["detail"]


def test_upload_size_limit(client, uploads, monkeypatch):
    monkeypatch.setattr(main.settings, "upload_max_bytes", 50)
    assert upload(client, b"a\n" + b"12345\n" * 20).status_code == 413


def test_upload_rate_limit_and_capacity(client, uploads, monkeypatch):
    monkeypatch.setattr(main, "_upload_limiter", RateLimiter(1))
    assert upload(client, SALES).status_code == 201
    assert upload(client, SALES).status_code == 429
    monkeypatch.setattr(main, "_upload_limiter", RateLimiter(0))
    monkeypatch.setattr(main.settings, "max_active_datasets", 0)
    assert upload(client, SALES).status_code == 503


def test_delete_removes_the_table(client, uploads, model):
    dataset = upload(client, SALES).json()
    table = re.search(r"FROM (t_\w+)", ask_sql(client, dataset, model)).group(1)
    headers = {"X-Dataset-Token": dataset["access_token"]}
    assert client.delete(f"/api/datasets/{dataset['id']}", headers={"X-Dataset-Token": "wrong"}).status_code == 404
    assert client.delete(f"/api/datasets/{dataset['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/datasets/{dataset['id']}", headers=headers).status_code == 404
    with get_ingest_engine().connect() as connection:
        assert not connection.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"uploads.{table}"}).scalar()


def test_expired_datasets_are_cleaned_up(client, uploads):
    from app.datasets import cleanup_expired
    dataset = upload(client, SALES).json()
    with get_ingest_engine().connect() as connection:
        connection.execute(text("UPDATE uploads.dataset_registry SET expires_at = now() - interval '1 second' "
                                "WHERE id = :id"), {"id": dataset["id"]})
        connection.commit()
    headers = {"X-Dataset-Token": dataset["access_token"]}
    assert client.get(f"/api/datasets/{dataset['id']}", headers=headers).status_code == 404
    assert cleanup_expired() >= 1


def test_schema_reports_upload_limits_and_demo_still_works(client, uploads):
    limits = client.get("/api/schema").json()["uploads"]
    assert limits["enabled"] is True and limits["max_rows"] > 0
    body = client.post("/api/query", json={"question": "How many orders are pending?"}).json()
    assert body["source"] == "demo" and body["rows"]


def test_dataset_id_format_is_validated(client, uploads):
    response = client.post("/api/query", json={"question": "Anything?", "dataset_id": "../../etc"})
    assert response.status_code == 422


PRICES = (b"Item,Price,Sold On\nTea,\"Rs 1,200\",03/04/2025\nRice,\"Rs 12,50,000\",28/02/2025\n"
          b"Salt,(50),01/12/2024\n")


def registry_count():
    with get_ingest_engine().connect() as connection:
        return connection.execute(text("SELECT count(*) FROM uploads.dataset_registry")).scalar()


def test_preview_checks_a_file_without_storing_it(client, uploads):
    before = registry_count()
    response = client.post("/api/datasets/preview?name=prices.csv", content=PRICES,
                           headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "prices.csv" and body["row_count"] == 3 and "access_token" not in body
    columns = {column["label"]: column for column in body["columns"]}
    assert columns["Price"]["type"] == "text" and columns["Price"]["choices"] == ["text", "integer", "decimal"]
    assert columns["Sold On"]["choices"] == ["text", "date_dmy"]
    assert registry_count() == before
    bad = client.post("/api/datasets/preview?name=bad.csv", content=b"a,a\n1,2\n", headers={"Content-Type": "text/csv"})
    assert bad.status_code == 400 and "unique" in bad.json()["detail"]


def test_reviewed_columns_are_imported_with_their_types(client, uploads, model):
    columns = [{"name": "item", "type": "text"}, {"name": "price_pkr", "type": "decimal"},
               {"name": "sold_on", "type": "date_dmy"}]
    response = client.post("/api/datasets?name=prices.csv", content=PRICES,
                           headers={"Content-Type": "text/csv", "X-Dataset-Columns": json.dumps(columns)})
    assert response.status_code == 201
    dataset = response.json()
    assert [(c["name"], c["label"], c["type"]) for c in dataset["columns"]] == [
        ("item", "Item", "text"), ("price_pkr", "Price", "decimal"), ("sold_on", "Sold On", "date")]
    model["Total and first sale?"] = "SELECT SUM(price_pkr) AS total, MIN(sold_on) AS first FROM {table}"
    assert ask(client, dataset, "Total and first sale?").json()["rows"] == [{"total": 1251150.0, "first": "2024-12-01"}]


@pytest.mark.parametrize("header, message", [
    ("{not json", "could not be read"),
    (json.dumps([{"name": "item", "type": "text"}]), "don't match"),
    (json.dumps([{"name": "item", "type": "text"}, {"name": "price", "type": "boolean"},
                 {"name": "sold_on", "type": "date_dmy"}]), "line 2"),
    (json.dumps([{"name": "item", "type": "text"}, {"name": "price\"); DROP TABLE x; --", "type": "text"},
                 {"name": "sold_on", "type": "text"}]), "valid column name"),
])
def test_bad_column_settings_are_rejected_and_nothing_is_stored(client, uploads, header, message):
    before = registry_count()
    response = client.post("/api/datasets?name=prices.csv", content=PRICES,
                           headers={"Content-Type": "text/csv", "X-Dataset-Columns": header})
    assert response.status_code == 400 and message in response.json()["detail"]
    assert registry_count() == before
