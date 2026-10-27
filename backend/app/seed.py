from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .database import Base
from .models import Customer, Order, OrderItem, Product


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        if db.scalar(select(Customer.id).limit(1)) is not None:
            return

        customers = [
            Customer(id=1, name="Ava Chen", email="ava@example.com", country="United States", created_at=date(2024, 1, 12)),
            Customer(id=2, name="Noah Patel", email="noah@example.com", country="United Kingdom", created_at=date(2024, 2, 4)),
            Customer(id=3, name="Mia Garcia", email="mia@example.com", country="Canada", created_at=date(2024, 2, 20)),
            Customer(id=4, name="Liam Ahmed", email="liam@example.com", country="Pakistan", created_at=date(2024, 3, 3)),
            Customer(id=5, name="Sofia Kim", email="sofia@example.com", country="United States", created_at=date(2024, 3, 16)),
            Customer(id=6, name="Ethan Brown", email="ethan@example.com", country="Australia", created_at=date(2024, 4, 1)),
            Customer(id=7, name="Zara Ali", email="zara@example.com", country="Pakistan", created_at=date(2024, 4, 11)),
            Customer(id=8, name="Oliver Smith", email="oliver@example.com", country="United Kingdom", created_at=date(2024, 5, 8)),
        ]
        products = [
            Product(id=1, name="Analytics Pro", category="Software", unit_price=Decimal("49.00")),
            Product(id=2, name="Team Workspace", category="Software", unit_price=Decimal("89.00")),
            Product(id=3, name="Data Connect", category="Add-on", unit_price=Decimal("29.00")),
            Product(id=4, name="Insights Pack", category="Add-on", unit_price=Decimal("59.00")),
            Product(id=5, name="Enterprise Plan", category="Software", unit_price=Decimal("249.00")),
            Product(id=6, name="Starter Plan", category="Software", unit_price=Decimal("19.00")),
        ]
        db.add_all(customers + products)
        db.flush()
        orders_data = [
            (1, 1, "2024-05-02", "completed", "147.00", [(2, 1), (3, 2)]),
            (2, 2, "2024-05-08", "completed", "178.00", [(2, 2)]),
            (3, 3, "2024-05-14", "pending", "98.00", [(1, 2)]),
            (4, 1, "2024-06-01", "completed", "249.00", [(5, 1)]),
            (5, 4, "2024-06-07", "cancelled", "59.00", [(4, 1)]),
            (6, 5, "2024-06-15", "completed", "265.00", [(2, 2), (3, 3)]),
            (7, 6, "2024-07-03", "pending", "48.00", [(3, 1), (6, 1)]),
            (8, 7, "2024-07-11", "completed", "498.00", [(5, 2)]),
            (9, 8, "2024-07-22", "completed", "108.00", [(1, 1), (4, 1)]),
            (10, 2, "2024-08-02", "completed", "249.00", [(5, 1)]),
            (11, 3, "2024-08-10", "pending", "89.00", [(2, 1)]),
            (12, 4, "2024-08-18", "completed", "146.00", [(4, 1), (3, 3)]),
            (13, 5, "2024-09-04", "completed", "98.00", [(1, 2)]),
            (14, 7, "2024-09-12", "cancelled", "19.00", [(6, 1)]),
        ]
        orders, items = [], []
        for order_id, customer_id, order_date, status, amount, line_items in orders_data:
            orders.append(Order(id=order_id, customer_id=customer_id, order_date=date.fromisoformat(order_date), status=status, total_amount=Decimal(amount)))
            for product_id, quantity in line_items:
                price = next(p.unit_price for p in products if p.id == product_id)
                items.append(OrderItem(order_id=order_id, product_id=product_id, quantity=quantity, unit_price=price))
        db.add_all(orders)
        db.flush()
        db.add_all(items)
        db.commit()

