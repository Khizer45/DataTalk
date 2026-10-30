from datetime import date, datetime
from decimal import Decimal

import pytest

from app import csv_import
from app.csv_import import CsvImportError, display_name, parse_csv


def types(parsed):
    return {column.name: column.type for column in parsed.columns}


def test_infers_each_type_and_nullability():
    parsed = parse_csv(b"id,price,active,day,seen_at,note\n"
                       b"1,9.50,true,2025-01-31,2025-01-31 10:15:00,hello\n"
                       b"2,,false,2025-02-01,2025-02-01T08:00,\n")
    assert types(parsed) == {"id": "integer", "price": "decimal", "active": "boolean", "day": "date",
                             "seen_at": "timestamp", "note": "text"}
    nullable = {column.name: column.nullable for column in parsed.columns}
    assert nullable["price"] and nullable["note"] and not nullable["id"]
    assert parsed.rows[0] == [1, Decimal("9.50"), True, date(2025, 1, 31), datetime(2025, 1, 31, 10, 15), "hello"]
    assert parsed.rows[1][1] is None and parsed.rows[1][5] is None


def test_doubtful_values_stay_text():
    parsed = parse_csv(b"zip,mixed,bad_date,us_date,money,huge\n"
                       b"007,12,2025-02-30,03/04/2025,\"1,200\",1e999\n"
                       b"042,abc,2025-03-01,04/05/2025,\"3,400\",2\n")
    assert set(types(parsed).values()) == {"text"}
    assert parsed.rows[0][0] == "007"  # leading zeros kept


def test_other_delimiters_quotes_and_line_breaks():
    parsed = parse_csv('name;comment\n"Smith, Ann";"said ""hi""\nthen left"\n'.encode())
    assert [column.name for column in parsed.columns] == ["name", "comment"]
    assert parsed.rows == [["Smith, Ann", 'said "hi"\nthen left']]


def test_encodings():
    assert parse_csv("\ufeffcity\nLahore\n".encode("utf-8")).columns[0].name == "city"  # BOM removed
    assert parse_csv("city\nCaf\u00e9 Town\n".encode("cp1252")).rows == [["Caf\u00e9 Town"]]


def test_headers_become_safe_names_and_keep_their_labels():
    parsed = parse_csv(b"Order Date,2024 Sales,order,,Amount ($),amount\n1,2,3,4,5,6\n")
    assert [column.name for column in parsed.columns] == [
        "order_date", "c_2024_sales", "order_", "column_4", "amount", "amount_2"]
    assert parsed.columns[0].label == "Order Date" and parsed.columns[3].label == "column_4"


def test_hostile_headers_and_values_are_just_data():
    parsed = parse_csv(b'"x""; DROP TABLE t; --",=1+1\n"Robert\'); DROP TABLE students;--",@cmd\n')
    assert parsed.columns[0].label == 'x"; DROP TABLE t; --'
    assert parsed.columns[0].name == "x_drop_table_t"
    assert parsed.rows == [["Robert'); DROP TABLE students;--", "@cmd"]]


def test_warnings_for_duplicates_and_empty_columns():
    parsed = parse_csv(b"a,b\n1,\n1,\n2,\n")
    assert any("exact copies" in warning for warning in parsed.warnings)
    assert any("'b' is empty" in warning for warning in parsed.warnings)
    assert len(parsed.rows) == 3  # duplicates are kept, not silently removed


def test_semicolons_inside_quoted_values_do_not_change_the_delimiter():
    parsed = parse_csv(b'id,note\n1,"a; b; c"\n2,"d; e"\n')
    assert [column.name for column in parsed.columns] == ["id", "note"]
    assert parsed.rows[0] == [1, "a; b; c"]


def test_blank_lines_are_skipped():
    assert len(parse_csv(b"a\n1\n\n2\n").rows) == 2


@pytest.mark.parametrize("data, message", [
    (b"", "empty"),
    (b"   \n", "empty"),
    (b"a,b\n1,\x002\n", "binary"),
    (b",,\n1,2,3\n", "first row"),
    (b"a,b\n", "no data rows"),
    (b"a,A\n1,2\n", "unique"),
    (b"a,b\n1,2,3\n", "header has 2"),
    (b'a,b\n"1,2\n', "valid CSV"),
    (b"a\n\xff\xfe\x81\n", "encoding"),
])
def test_rejects_bad_files(data, message):
    with pytest.raises(CsvImportError, match=message):
        parse_csv(data)


def test_limits(monkeypatch):
    monkeypatch.setattr(csv_import.settings, "upload_max_bytes", 20)
    with pytest.raises(CsvImportError, match="larger than"):
        parse_csv(b"a\n" + b"1\n" * 20)
    monkeypatch.setattr(csv_import.settings, "upload_max_bytes", 10_000)
    monkeypatch.setattr(csv_import.settings, "upload_max_rows", 2)
    with pytest.raises(CsvImportError, match="more than 2 rows"):
        parse_csv(b"a\n1\n2\n3\n")
    monkeypatch.setattr(csv_import.settings, "upload_max_columns", 2)
    with pytest.raises(CsvImportError, match="limit is 2"):
        parse_csv(b"a,b,c\n1,2,3\n")
    monkeypatch.setattr(csv_import.settings, "upload_max_cell_chars", 3)
    with pytest.raises(CsvImportError, match="longer than 3"):
        parse_csv(b"a\nabcd\n")


def test_display_name_is_plain_text():
    assert display_name("C:\\Users\\me\\sales\x07 2025.csv") == "sales 2025.csv"
    assert display_name("../../etc/passwd") == "passwd"
    assert display_name("") == "Uploaded data"


# Reviewing columns before import: the uploader can rename columns and choose how values are read.

REVIEW = (b"Order Ref,Amount,Paid,Order Date,Shipped\n"
          b"007,\"$1,200.50\",yes,03/04/2025,2025-04-03 10:15\n"
          b"042,\"Rs 1,20,000\",no,28/02/2025,\n"
          b"105,(350),Y,01/12/2024,2025-01-02\n")


def test_preview_offers_only_types_every_value_fits():
    preview = csv_import.preview_csv(REVIEW)
    columns = {column["label"]: column for column in preview["columns"]}
    assert preview["row_count"] == 3
    assert columns["Order Ref"]["type"] == "text"  # leading zeros: inference keeps text...
    assert "integer" in columns["Order Ref"]["choices"]  # ...but the uploader may choose a number
    assert columns["Amount"]["choices"] == ["text", "decimal"]
    assert columns["Paid"]["choices"] == ["text", "boolean"]
    assert columns["Order Date"]["choices"] == ["text", "date_dmy"]  # 28/02 rules out month-first
    assert columns["Shipped"]["choices"] == ["text", "timestamp"]
    assert columns["Amount"]["samples"] == ["$1,200.50", "Rs 1,20,000", "(350)"]
    assert columns["Shipped"]["nullable"] and not columns["Amount"]["nullable"]


def test_reviewed_names_and_types_are_applied():
    overrides = [{"name": "order_ref", "type": "text"}, {"name": "amount_pkr", "type": "decimal"},
                 {"name": "paid", "type": "boolean"}, {"name": "ordered_on", "type": "date_dmy"},
                 {"name": "shipped_at", "type": "timestamp"}]
    parsed = parse_csv(REVIEW, overrides)
    assert [(c.name, c.label, c.type) for c in parsed.columns] == [
        ("order_ref", "Order Ref", "text"), ("amount_pkr", "Amount", "decimal"), ("paid", "Paid", "boolean"),
        ("ordered_on", "Order Date", "date"), ("shipped_at", "Shipped", "timestamp")]
    assert parsed.rows[0] == ["007", Decimal("1200.50"), True, date(2025, 4, 3), datetime(2025, 4, 3, 10, 15)]
    assert parsed.rows[1][1:4] == [Decimal("120000"), False, date(2025, 2, 28)] and parsed.rows[1][4] is None
    assert parsed.rows[2][1] == Decimal("-350") and parsed.rows[2][4] == datetime(2025, 1, 2)


@pytest.mark.parametrize("value, number", [
    ("1,234,567.5", "1234567.5"), ("12,34,567", "1234567"), ("-$5", "-5"), ("$-5", "-5"), ("5 USD", "5"),
    ("€ 9.99", "9.99"), ("+7", "7"), ("(1,000)", "-1000"),
    ("1,23", None), ("1.234,5", None), ("12%", None), ("(-5)", None), ("$", None), ("()", None), ("-", None),
    ("1,2345", None), ("abc", None),
])
def test_money_and_grouped_numbers(value, number):
    assert csv_import._number_text(value) == number


def test_month_first_dates_and_whole_numbers():
    parsed = parse_csv(b"day,qty\n12/31/2024,\"1,500\"\n01/02/2025,7\n",
                       [{"name": "day", "type": "date_mdy"}, {"name": "qty", "type": "integer"}])
    assert [row for row in parsed.rows] == [[date(2024, 12, 31), 1500], [date(2025, 1, 2), 7]]


@pytest.mark.parametrize("overrides, message", [
    ([{"name": "a", "type": "text"}], "don't match"),
    ([{"name": "a", "type": "text"}, {"name": "b", "type": "money"}], "don't match"),
    ([{"name": "a", "type": "text"}, "b"], "don't match"),
    ("not a list", "don't match"),
    ([{"name": "a", "type": "text"}, {"name": "Bad Name", "type": "text"}], "isn't a valid column name"),
    ([{"name": "a", "type": "text"}, {"name": "1st", "type": "text"}], "isn't a valid column name"),
    ([{"name": "a", "type": "text"}, {"name": "x\"; DROP TABLE t; --", "type": "text"}], "isn't a valid column name"),
    ([{"name": "a", "type": "text"}, {"name": "select", "type": "text"}], "reserved"),
    ([{"name": "a", "type": "text"}, {"name": "a", "type": "text"}], "unique"),
    ([{"name": "a", "type": "text"}, {"name": "b", "type": "integer"}], "line 3"),
])
def test_bad_overrides_are_rejected_clearly(overrides, message):
    with pytest.raises(CsvImportError, match=message):
        parse_csv(b"a,b\nx,12\ny,1.5\n", overrides)


def test_a_value_that_does_not_fit_names_the_column_and_line():
    with pytest.raises(CsvImportError) as error:
        parse_csv(b"happened\n2025-01-01\n31/12/2024\n", [{"name": "happened", "type": "date"}])
    assert str(error.value) == ("Column 'happened': the value '31/12/2024' on line 3 can't be read as a date "
                                "(YYYY-MM-DD). Choose another type, or fix the file.")


def test_impossible_dates_and_times_do_not_fit():
    preview = csv_import.preview_csv(b"d,t\n31/02/2025,2025-01-01 25:00\n")
    assert [column["choices"] for column in preview["columns"]] == [["text"], ["text"]]
