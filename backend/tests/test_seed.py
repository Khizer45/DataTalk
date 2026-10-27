from collections import defaultdict
from decimal import Decimal

import pytest

from app.seed import END, START, generate_demo_data


@pytest.fixture(scope="module")
def data():
    return generate_demo_data()


def test_data_is_identical_on_every_run(data):
    assert generate_demo_data() == data


def test_size(data):
    # Changing these numbers changes the evaluation baseline; update eval/results when you do.
    assert {name: len(rows) for name, rows in data.items()} == {
        "customers": 250, "products": 13, "orders": 2431, "order_items": 3604}


def test_order_totals_match_their_lines(data):
    totals = defaultdict(Decimal)
    for item in data["order_items"]:
        totals[item["order_id"]] += item["unit_price"] * item["quantity"]
    assert all(order["total_amount"] == totals[order["id"]] for order in data["orders"])


def test_only_completed_orders_have_a_shipped_date(data):
    assert all((order["shipped_date"] is None) == (order["status"] != "completed") for order in data["orders"])
    assert all(order["shipped_date"] is None or order["order_date"] < order["shipped_date"] <= END
               for order in data["orders"])


def test_orders_are_inside_the_period_and_after_signup(data):
    signup = {customer["id"]: customer["created_at"] for customer in data["customers"]}
    assert all(START <= order["order_date"] <= END for order in data["orders"])
    assert all(order["order_date"] >= signup[order["customer_id"]] for order in data["orders"])


def test_contains_edge_cases_the_evaluation_relies_on(data):
    ordered_products = {item["product_id"] for item in data["order_items"]}
    never_ordered = [p["name"] for p in data["products"] if p["id"] not in ordered_products]
    buyers = {order["customer_id"] for order in data["orders"]}
    assert never_ordered == ["Legacy Plan"]
    assert any(customer["id"] not in buyers for customer in data["customers"])
    assert len({customer["name"] for customer in data["customers"]}) == len(data["customers"])
