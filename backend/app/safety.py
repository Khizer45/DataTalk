from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import traverse_scope


class UnsafeQueryError(ValueError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        # False when the SQL tries to change data: asking the model again could turn a request to delete
        # or update into an unrelated SELECT that looks like an answer.
        self.retryable = retryable


# Function nodes as sqlglot parses them for PostgreSQL. Anything not listed is rejected, which keeps
# server-side functions (file access, session control, SQL-in-a-string helpers, metadata) out of reach.
# The database role is the primary control; this list is defense in depth.
ALLOWED_FUNCTIONS = {
    # aggregates
    "Count", "Sum", "Avg", "Min", "Max", "Stddev", "StddevPop", "StddevSamp", "Variance", "VariancePop",
    "PercentileCont", "PercentileDisc", "Mode", "GroupConcat", "ArrayAgg", "LogicalAnd", "LogicalOr",
    # math
    "Abs", "Ceil", "Floor", "Round", "Sqrt", "Pow", "Ln", "Log", "Exp", "Sign",
    # date and time
    "TimestampTrunc", "DateTrunc", "Extract", "TimeToStr", "StrToTime", "StrToDate", "Date", "CurrentDate",
    "CurrentTimestamp", "CurrentTime", "DateAdd", "DateSub", "DateDiff", "TsOrDsToDate",
    # conditionals, casts and subquery tests
    "Case", "If", "Coalesce", "Nullif", "Greatest", "Least", "Cast", "TryCast", "Exists",
    # strings
    "Lower", "Upper", "Trim", "Length", "Concat", "ConcatWs", "Substring", "Replace", "Initcap", "Left",
    "Right", "Pad", "StrPosition", "SplitPart",
    # window functions
    "RowNumber", "Rank", "DenseRank", "PercentRank", "CumeDist", "Ntile", "Lag", "Lead", "FirstValue",
    "LastValue", "NthValue",
    # set-returning helpers used for calendars and gap filling
    "GenerateSeries", "ExplodingGenerateSeries",
}
# PostgreSQL functions that sqlglot does not model and parses as anonymous calls.
ALLOWED_ANONYMOUS_FUNCTIONS = {"age", "date_part", "to_char", "to_date", "make_date", "justify_interval",
                               "btrim", "ltrim", "rtrim", "char_length", "width_bucket"}


def _function_name(node: exp.Func) -> str:
    return node.name if isinstance(node, exp.Anonymous) else node.sql_name()


def _check_functions(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Func):
        if isinstance(node, exp.Connector):  # AND / OR are modelled as functions
            continue
        if isinstance(node, exp.Anonymous):
            allowed = node.name.casefold() in ALLOWED_ANONYMOUS_FUNCTIONS
        else:
            allowed = type(node).__name__ in ALLOWED_FUNCTIONS
        if not allowed:
            raise UnsafeQueryError(f"The function {_function_name(node)}() is not allowed.")


def _check_tables(tree: exp.Expression, allowed_tables: Iterable[str]) -> None:
    """Every table must be allowed, unless the name refers to a CTE that is visible where it is used.

    CTE names are resolved by scope, as PostgreSQL does: a non-recursive CTE cannot see itself, so in
    WITH t AS (SELECT * FROM t) the inner t is the real table t; a CTE defined inside a subquery is not visible
    outside it; and unquoted names are folded to lower case first, so WITH "T" AS (...) does not hide a table t.
    """
    permitted = {name.casefold() for name in allowed_tables}
    tree = normalize_identifiers(tree.copy(), dialect="postgres")
    tables = [table for table in tree.find_all(exp.Table) if not isinstance(table.this, exp.Func)]
    if any(table.db or table.catalog for table in tables):
        raise UnsafeQueryError("Schema-qualified table access is not allowed.")
    try:
        scopes = traverse_scope(tree)
    except SqlglotError as exc:
        raise UnsafeQueryError("The query's structure could not be checked.") from exc
    checked = set()
    for scope in scopes:
        for table in scope.tables:
            if isinstance(table.this, exp.Func):  # table function such as generate_series(); checked above
                continue
            checked.add(id(table))
            if table.name not in scope.cte_sources and table.name not in permitted:
                raise UnsafeQueryError(f"The query references a table outside the exposed schema: {table.name}.")
    if any(id(table) not in checked for table in tables):  # a table no scope accounts for: refuse rather than guess
        raise UnsafeQueryError("The query's structure could not be checked.")


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string and node.this.isdigit():
        return int(node.this)
    return None


def _apply_row_limit(tree: exp.Query, row_limit: int) -> exp.Query:
    """Cap the rows returned without loosening a smaller LIMIT the query already has."""
    existing = tree.args.get("limit")
    if isinstance(existing, exp.Limit):
        current = _literal_int(existing.expression)
    elif isinstance(existing, exp.Fetch):
        current = _literal_int(existing.args.get("count"))
    else:
        current = None
    if current is not None and current <= row_limit:
        return tree
    return tree.limit(row_limit)


def validate_read_query(sql: str, allowed_tables: Iterable[str], row_limit: int) -> str:
    """Parse a single read query, restrict it to known tables and safe functions, then cap its output."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQueryError("The generated SQL could not be parsed.") from exc

    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        raise UnsafeQueryError("Only one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Query) or isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        raise UnsafeQueryError("Only read-only SELECT queries are allowed.", retryable=False)
    if any(tree.find(kind) for kind in (exp.DDL, exp.DML, exp.Into, exp.Lock)):
        raise UnsafeQueryError("The query contains a write or locking operation.", retryable=False)

    _check_functions(tree)
    _check_tables(tree, allowed_tables)
    return _apply_row_limit(tree, row_limit).sql(dialect="postgres")
