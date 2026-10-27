"""Synthetic demo data: a small online software shop.

Rows are generated from a fixed random seed, so every install gets identical data and
evaluation results are reproducible. Names and emails are invented; no real people or
companies are represented.
"""
import random
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal
from itertools import accumulate

from sqlalchemy import inspect, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .database import Base
from .models import Customer, Order, OrderItem, Product

DATASET_INFO = {
    "name": "Demo online shop",
    "description": ("Synthetic sales data for a small software shop: customers, products, orders and "
                    "order lines from January 2024 to December 2025. Invented names; no real people."),
    "synthetic": True,
    "definitions": ["Revenue counts completed orders only; pending and cancelled orders are excluded.",
                    "shipped_date is empty (NULL) until an order ships."],
}

SEED = 7
START = date(2024, 1, 1)
END = date(2025, 12, 31)
CUSTOMER_COUNT = 250
INACTIVE_CUSTOMERS = 12  # signed up but never ordered

FIRST_NAMES = ["Ava", "Noah", "Mia", "Liam", "Sofia", "Ethan", "Zara", "Oliver", "Amara", "Hiro",
               "Fatima", "Lucas", "Priya", "Omar", "Elena", "Kenji", "Aisha", "Mateo", "Chloe", "Ravi",
               "Hana", "Diego", "Leila", "Samuel", "Ingrid", "Tariq", "Nadia", "Felix", "Mei", "Yusuf"]
LAST_NAMES = ["Chen", "Patel", "Garcia", "Ahmed", "Kim", "Brown", "Ali", "Smith", "Okafor", "Tanaka",
              "Khan", "Silva", "Sharma", "Haddad", "Rossi", "Sato", "Hussain", "Lopez", "Martin", "Iyer",
              "Park", "Torres", "Rahman", "Johnson", "Larsen", "Malik", "Novak", "Weber", "Wong", "Yilmaz"]
COUNTRIES = [("United States", 26), ("United Kingdom", 14), ("Pakistan", 12), ("Canada", 10),
             ("Germany", 10), ("India", 10), ("United Arab Emirates", 10), ("Australia", 8)]
SEGMENTS = [("Consumer", 60), ("Small business", 30), ("Enterprise", 10)]
CHANNELS = [("Website", 55), ("Mobile app", 35), ("Partner", 10)]

# (name, category, unit price, how often it is bought). Legacy Plan is never ordered on purpose.
PRODUCTS = [
    ("Starter Plan", "Software", "19.00", 22),
    ("Analytics Pro", "Software", "49.00", 18),
    ("Team Workspace", "Software", "89.00", 12),
    ("Enterprise Plan", "Software", "249.00", 5),
    ("Legacy Plan", "Software", "9.00", 0),
    ("Data Connect", "Add-on", "29.00", 14),
    ("Insights Pack", "Add-on", "59.00", 9),
    ("Extra Storage", "Add-on", "15.00", 16),
    ("Priority Support", "Add-on", "99.00", 6),
    ("Onboarding Session", "Service", "150.00", 5),
    ("Custom Report", "Service", "300.00", 3),
    ("Training Workshop", "Service", "450.00", 2),
    ("Data Audit", "Service", "800.00", 1),
]


# Only rng.random() is guaranteed to give the same sequence on every Python version, so all choices are built
# on it (random.choices, sample and randint may change between versions).
def _below(rng: random.Random, n: int) -> int:
    return min(int(rng.random() * n), n - 1)


def _weighted(rng: random.Random, weights: list[int]) -> int:
    cumulative = list(accumulate(weights))
    return bisect_right(cumulative, rng.random() * cumulative[-1])


def _pick(rng: random.Random, options: list[tuple[str, int]]) -> str:
    return options[_weighted(rng, [weight for _, weight in options])][0]


def _sample(rng: random.Random, items: list, k: int) -> list:
    pool = list(items)
    for i in range(k):
        j = i + _below(rng, len(pool) - i)
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:k]


def generate_demo_data(seed: int = SEED) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    products = [{"id": i, "name": name, "category": category, "unit_price": Decimal(price)}
                for i, (name, category, price, _) in enumerate(PRODUCTS, start=1)]
    base_weights = [weight for *_, weight in PRODUCTS]
    # Enterprise customers lean towards the Enterprise Plan and services.
    enterprise_weights = [weight * (4 if category == "Service" or name == "Enterprise Plan" else 1)
                          for name, category, _, weight in PRODUCTS]

    names = _sample(rng, [f"{first} {last}" for first in FIRST_NAMES for last in LAST_NAMES], CUSTOMER_COUNT)
    signup_span = (date(2025, 11, 30) - date(2023, 6, 1)).days
    customers = []
    for i, name in enumerate(names, start=1):
        customers.append({
            "id": i, "name": name, "email": f"{name.lower().replace(' ', '.')}.{i}@example.com",
            "country": _pick(rng, COUNTRIES), "segment": _pick(rng, SEGMENTS),
            "created_at": date(2023, 6, 1) + timedelta(days=_below(rng, signup_span + 1)),
        })

    inactive = set(_sample(rng, list(range(1, CUSTOMER_COUNT + 1)), INACTIVE_CUSTOMERS))
    buyers = sorted((c for c in customers if c["id"] not in inactive), key=lambda c: (c["created_at"], c["id"]))
    signup_dates = [c["created_at"] for c in buyers]
    # A few customers buy far more often than the rest.
    cumulative = list(accumulate([1, 1, 1, 2, 3, 6][_below(rng, 6)] for _ in buyers))

    orders, items = [], []
    total_days = (END - START).days
    day = START
    while day <= END:
        growth = 1 + 0.45 * (day - START).days / total_days
        season = 1.35 if day.month in (11, 12) else 0.85 if day.month in (1, 2) else 1.0
        expected = 2.6 * growth * season
        count = int(expected) + (1 if rng.random() < expected - int(expected) else 0)
        eligible = bisect_right(signup_dates, day)
        for _ in range(count if eligible else 0):
            customer = buyers[bisect_right(cumulative, rng.random() * cumulative[eligible - 1])]
            order_id = len(orders) + 1
            channel = _pick(rng, CHANNELS)
            weights = enterprise_weights if customer["segment"] == "Enterprise" else base_weights
            line_count = 1 + _weighted(rng, [60, 30, 10])
            chosen: set[int] = set()
            while len(chosen) < line_count:
                chosen.add(_weighted(rng, weights))
            total = Decimal("0.00")
            for index in sorted(chosen):
                product = products[index]
                if customer["segment"] == "Consumer":
                    quantity = 1 + _weighted(rng, [75, 20, 5])
                else:
                    quantity = 1 + _below(rng, 5)
                items.append({"id": len(items) + 1, "order_id": order_id, "product_id": product["id"],
                              "quantity": quantity, "unit_price": product["unit_price"]})
                total += product["unit_price"] * quantity

            roll = rng.random()
            if (END - day).days < 14 and roll < 0.5:
                status = "pending"
            elif roll < (0.12 if channel == "Partner" else 0.06):
                status = "cancelled"
            else:
                status = "completed"
            shipped = day + timedelta(days=1 + _below(rng, 6)) if status == "completed" else None
            if shipped and shipped > END:
                status, shipped = "pending", None

            orders.append({"id": order_id, "customer_id": customer["id"], "order_date": day, "status": status,
                           "channel": channel, "total_amount": total, "shipped_date": shipped})
        day += timedelta(days=1)

    return {"customers": customers, "products": products, "orders": orders, "order_items": items}


def _check_schema_is_current(engine: Engine) -> None:
    """create_all() never alters existing tables, so an older demo schema must be reset explicitly."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        missing = {column.name for column in table.columns} - {c["name"] for c in inspector.get_columns(table.name)}
        if missing:
            raise RuntimeError(
                f"The database has an older DataTalk demo schema ({table.name} is missing "
                f"{', '.join(sorted(missing))}). Reset it - locally run `docker compose down -v` - then run setup again."
            )


def initialize_database(engine: Engine) -> None:
    _check_schema_is_current(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        if db.scalar(select(Customer.id).limit(1)) is not None:
            return
        data = generate_demo_data()
        for model, rows in ((Customer, data["customers"]), (Product, data["products"]),
                            (Order, data["orders"]), (OrderItem, data["order_items"])):
            db.execute(insert(model), rows)
        db.commit()
