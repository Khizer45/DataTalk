from sqlalchemy.exc import DBAPIError

from .config import settings

# SQLSTATE codes → (HTTP status, message safe to show users). Driver text, SQL and schema details stay in the logs.
_KNOWN_ERRORS = {
    "42703": (422, "The query referred to a column that doesn't exist. Try rephrasing using the column names in the schema."),
    "42P01": (422, "The query referred to a table that doesn't exist. Try rephrasing using the tables in the schema."),
    "42883": (422, "The query used a function or operator that doesn't fit these columns. Try rephrasing the question."),
    "42803": (422, "The query grouped data incorrectly. Try rephrasing the question."),
    "42501": (400, "The query tried to read data DataTalk is not allowed to access."),
    "25006": (400, "The query tried to change data, which DataTalk never allows."),
}


def describe_database_error(exc: Exception) -> tuple[int, str]:
    code = getattr(getattr(exc, "orig", None), "sqlstate", None) if isinstance(exc, DBAPIError) else None
    if code in _KNOWN_ERRORS:
        return _KNOWN_ERRORS[code]
    if code == "57014":
        seconds = settings.statement_timeout_ms / 1000
        return 422, f"The query took longer than {seconds:g} seconds and was stopped. Try a narrower question."
    if code and code.startswith("22"):
        return 422, "The query failed while converting or comparing values. Try rephrasing the question."
    if code and code.startswith("08"):
        return 503, "The database is temporarily unavailable. Please try again shortly."
    return 422, "The query could not be completed. Try rephrasing the question."
