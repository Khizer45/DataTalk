import json

from .config import settings
from .database import execute_read_query
from .generator import generate_sql, get_schema_context
from .safety import validate_read_query


# NOTE: a placeholder set. In rules mode the generator returns these same reference queries, so a 100 % score
# here says nothing about accuracy. A proper evaluation dataset is planned (see README, "Known limitations").
CASES = [
    {"question": "How many orders are pending?", "reference_sql": "SELECT COUNT(*) AS pending_orders FROM orders WHERE status = 'pending'"},
    {"question": "Show the top 5 products by revenue", "reference_sql": "SELECT p.name AS product, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.name ORDER BY revenue DESC LIMIT 5"},
    {"question": "Show revenue by customer country", "reference_sql": "SELECT c.country, SUM(o.total_amount) AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY c.country ORDER BY revenue DESC"},
]


def run_evaluation(max_rows: int) -> dict:
    tables, schema = get_schema_context()
    allowed = [t["name"] for t in tables]
    results = []
    for case in CASES:
        generated_sql = None
        try:
            candidate, generator, _ = generate_sql(case["question"], schema)
            generated_sql = validate_read_query(candidate, allowed, max_rows)
            candidate_rows = execute_read_query(generated_sql)
            expected_rows = execute_read_query(validate_read_query(case["reference_sql"], allowed, max_rows))
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
                            "error": type(exc).__name__})
    total = len(results)
    return {"total_cases": total,
            "sql_correctness": sum(r["sql_correct"] for r in results) / total if total else 0,
            "execution_success": sum(r["execution_success"] for r in results) / total if total else 0,
            "response_accuracy": sum(r["response_accuracy"] for r in results) / total if total else 0,
            "results": results}


if __name__ == "__main__":
    print(json.dumps(run_evaluation(settings.max_result_rows), indent=2, default=str))
