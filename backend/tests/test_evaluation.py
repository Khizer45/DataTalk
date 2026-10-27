import json
from datetime import date, datetime
from decimal import Decimal

from app.evaluation import EVAL_DIR, normalize_value, results_match


def test_equivalent_values_compare_equal():
    assert normalize_value(Decimal("5.00")) == normalize_value(5) == normalize_value("5")
    assert normalize_value("2025-03") == normalize_value(date(2025, 3, 1)) == normalize_value(datetime(2025, 3, 1))
    assert normalize_value(None) is None


def test_row_order_is_ignored_unless_it_matters():
    expected = [{"country": "A", "n": 1}, {"country": "B", "n": 2}]
    reversed_rows = list(reversed(expected))
    assert results_match(reversed_rows, expected)
    assert not results_match(reversed_rows, expected, order_matters=True)


def test_column_names_and_extra_columns_are_allowed():
    expected = [{"country": "A"}, {"country": "B"}]
    actual = [{"c": "A", "revenue": 10}, {"c": "B", "revenue": 20}]
    assert results_match(actual, expected)


def test_columns_must_pair_up_row_by_row():
    expected = [{"country": "A", "n": 1}, {"country": "B", "n": 2}]
    swapped = [{"country": "A", "n": 2}, {"country": "B", "n": 1}]
    assert not results_match(swapped, expected)


def test_row_count_must_match():
    assert not results_match([{"n": 1}], [{"n": 1}, {"n": 1}])


def test_cases_file_is_well_formed():
    cases = json.loads((EVAL_DIR / "cases.json").read_text())["cases"]
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["expected"] in ("answer", "decline")
        assert case["question"].strip()
        assert ("sql" in case) == (case["expected"] == "answer")
