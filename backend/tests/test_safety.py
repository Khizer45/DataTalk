import pytest

from app.safety import UnsafeQueryError, validate_read_query

TABLES = ["customers", "products", "orders", "order_items"]


@pytest.mark.parametrize("sql", [
    "SELECT name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
    "WITH totals AS (SELECT customer_id, SUM(total_amount) AS spend FROM orders GROUP BY 1) SELECT * FROM totals",
    "SELECT to_char(date_trunc('month', order_date), 'YYYY-MM') AS month, COUNT(*) FROM orders GROUP BY 1",
    "SELECT DATE(order_date), EXTRACT(YEAR FROM order_date), order_date::date FROM orders",
    "SELECT name, RANK() OVER (ORDER BY unit_price DESC) FROM products",
    "SELECT d::date FROM generate_series('2025-01-01'::date, '2025-12-01'::date, INTERVAL '1 month') AS d",
    "SELECT COUNT(*) FILTER (WHERE status = 'cancelled'), ROUND(AVG(total_amount), 2) FROM orders",
    "SELECT country FROM customers UNION SELECT category FROM products",
])
def test_accepts_common_analytics_sql(sql):
    validate_read_query(sql, TABLES, 100)


def test_keeps_a_smaller_limit():
    sql = validate_read_query("SELECT name FROM products ORDER BY unit_price DESC LIMIT 5", TABLES, 101)
    assert sql.endswith("LIMIT 5")


def test_caps_a_larger_limit():
    assert validate_read_query("SELECT name FROM products LIMIT 1000", TABLES, 101).endswith("LIMIT 101")


def test_adds_a_limit_when_missing():
    assert validate_read_query("SELECT name FROM products", TABLES, 101).endswith("LIMIT 101")


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "UPDATE products SET unit_price = 1",
    "DROP TABLE orders",
    "SELECT 1; SELECT 2",
    "",
])
def test_rejects_anything_but_a_single_select(sql):
    with pytest.raises(UnsafeQueryError):
        validate_read_query(sql, TABLES, 100)


def test_rejects_tables_outside_the_exposed_list():
    with pytest.raises(UnsafeQueryError, match="outside the exposed schema"):
        validate_read_query("SELECT * FROM staff", TABLES, 100)


def test_rejects_schema_qualified_tables():
    with pytest.raises(UnsafeQueryError, match="Schema-qualified"):
        validate_read_query("SELECT * FROM public.orders", TABLES, 100)


def test_rejects_functions_outside_the_allowlist():
    with pytest.raises(UnsafeQueryError, match="not allowed"):
        validate_read_query("SELECT md5(name) FROM customers", TABLES, 100)
