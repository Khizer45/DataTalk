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


def test_only_fixable_rejections_are_marked_retryable():
    with pytest.raises(UnsafeQueryError) as write_attempt:
        validate_read_query("DELETE FROM orders", TABLES, 100)
    with pytest.raises(UnsafeQueryError) as unknown_table:
        validate_read_query("SELECT * FROM staff", TABLES, 100)
    assert write_attempt.value.retryable is False and unknown_table.value.retryable is True


@pytest.mark.parametrize("sql", [
    # A CTE named after a hidden table: inside its own body the name is still the real table.
    "WITH users AS (SELECT * FROM users) SELECT * FROM users",
    "WITH pg_class AS (SELECT * FROM pg_class) SELECT relname FROM pg_class",
    "WITH a AS (SELECT 1), users AS (SELECT * FROM users) SELECT * FROM users",
    # A CTE defined inside a subquery is not visible outside it.
    "SELECT id FROM orders WHERE EXISTS (WITH users AS (SELECT 1) SELECT 1) UNION ALL SELECT id FROM users",
    # Quoted names keep their case; unquoted names are folded: "USERS" does not hide users.
    'WITH "USERS" AS (SELECT 1) SELECT * FROM USERS',
    "SELECT * FROM (WITH users AS (SELECT 1) SELECT * FROM orders) o JOIN users u ON true",
])
def test_cte_names_cannot_hide_other_tables(sql):
    with pytest.raises(UnsafeQueryError, match="outside the exposed schema"):
        validate_read_query(sql, TABLES, 100)


@pytest.mark.parametrize("sql", [
    "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 5) SELECT * FROM n",
    "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT COUNT(*) FROM b",
    'WITH "Totals" AS (SELECT 1 AS x) SELECT * FROM "Totals"',
    "WITH orders AS (SELECT * FROM orders WHERE status = 'paid') SELECT COUNT(*) FROM orders",
])
def test_ctes_that_only_use_allowed_tables_still_work(sql):
    validate_read_query(sql, TABLES, 100)
