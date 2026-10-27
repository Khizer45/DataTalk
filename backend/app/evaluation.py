from sqlalchemy import text

from .config import settings
from .database import engine
from .generator import generate_sql, get_schema_context
from .safety import validate_read_query


CASES = [
    {"question": "How many orders are pending?", "reference_sql": "SELECT COUNT(*) AS pending_orders FROM orders WHERE status = 'pending'"},
    {"question": "Show the top 5 products by revenue", "reference_sql": "SELECT p.name AS product, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.name ORDER BY revenue DESC LIMIT 5"},
    {"question": "Show revenue by customer country", "reference_sql": "SELECT c.country, SUM(o.total_amount) AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY c.country ORDER BY revenue DESC"},
]


def _fetch(sql: str) -> list[dict]:
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(settings.statement_timeout_ms)}")
        result = connection.execute(text(sql))
        return [dict(row) for row in result.mappings().all()]


def run_evaluation(max_rows: int) -> dict:
    tables, schema = get_schema_context()
    allowed = [t["name"] for t in tables]
    results = []
    for case in CASES:
        generated_sql = None
        try:
            candidate, generator, _ = generate_sql(case["question"], schema)
            generated_sql = validate_read_query(candidate, allowed, max_rows)
            candidate_rows = _fetch(generated_sql)
            reference_sql = validate_read_query(case["reference_sql"], allowed, max_rows)
            expected_rows = _fetch(reference_sql)
            accurate = candidate_rows == expected_rows
            results.append({"question": case["question"], "generator": generator, "sql": generated_sql,
                            "sql_correct": accurate, "execution_success": True,
                            "response_accuracy": 1.0 if accurate else 0.0,
                            "expected_rows": len(expected_rows), "actual_rows": len(candidate_rows),
                            "error": None})
        except Exception as exc:
            results.append({"question": case["question"], "generator": "n/a", "sql": generated_sql,
                            "sql_correct": False, "execution_success": False,
                            "response_accuracy": 0.0, "expected_rows": None, "actual_rows": None,
                            "error": str(exc)})
    total = len(results)
    return {"total_cases": total,
            "sql_correctness": sum(r["sql_correct"] for r in results) / total if total else 0,
            "execution_success": sum(r["execution_success"] for r in results) / total if total else 0,
            "response_accuracy": sum(r["response_accuracy"] for r in results) / total if total else 0,
            "results": results}

