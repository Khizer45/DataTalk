from collections.abc import Iterable

import sqlglot
from sqlglot import exp


class UnsafeQueryError(ValueError):
    pass


def validate_read_query(sql: str, allowed_tables: Iterable[str], max_rows: int) -> str:
    """Parse a single read query, restrict it to known tables, then cap its output."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQueryError("The generated SQL could not be parsed safely.") from exc

    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        raise UnsafeQueryError("Only one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Query) or isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        raise UnsafeQueryError("Only read-only SELECT queries are allowed.")
    if any(tree.find(kind) for kind in (exp.DDL, exp.DML, exp.Into, exp.Lock)):
        raise UnsafeQueryError("The query contains a write or locking operation.")

    permitted = {name.casefold() for name in allowed_tables}
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog:
            raise UnsafeQueryError("Schema-qualified table access is not allowed.")
        if table.name.casefold() not in permitted:
            raise UnsafeQueryError(f"The query references a table outside the exposed schema: {table.name}.")

    tree = tree.limit(max_rows)
    return tree.sql(dialect="postgres")

