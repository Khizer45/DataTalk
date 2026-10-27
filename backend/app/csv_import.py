"""Reading an uploaded CSV safely: limits, header clean-up and cautious type inference.

Nothing here touches the database. The filename, headers and cell values are untrusted: headers become safe
column names (the original text is kept as a label), and values are returned as typed Python objects that are
later inserted as parameters, never as SQL text.
"""
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .config import settings

# SQL type for each inferred column type. Only these fixed names ever reach CREATE TABLE.
SQL_TYPES = {"integer": "bigint", "decimal": "numeric", "boolean": "boolean", "date": "date",
             "timestamp": "timestamp", "text": "text"}

_DELIMITERS = ",;\t|"
_INTEGER = re.compile(r"^[+-]?(0|[1-9]\d*)$")
_DECIMAL = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_LEADING_ZERO = re.compile(r"^[+-]?0\d")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?$")
_BOOLEANS = {"true": True, "false": False}
_BIGINT = 2 ** 63
# Words a model would have to quote; column names that equal one get a trailing underscore.
_RESERVED = {"all", "and", "any", "as", "asc", "case", "check", "column", "constraint", "create", "default",
             "desc", "distinct", "do", "else", "end", "false", "for", "foreign", "from", "grant", "group", "having",
             "in", "into", "is", "join", "limit", "not", "null", "offset", "on", "or", "order", "primary",
             "references", "select", "table", "then", "to", "true", "union", "unique", "user", "using", "when",
             "where", "with"}


class CsvImportError(ValueError):
    """A problem with the uploaded file that the user can fix. The message is safe to show."""


@dataclass
class Column:
    name: str      # safe column name used in SQL, e.g. "order_date"
    label: str     # the original header text, for display
    type: str      # a key of SQL_TYPES
    nullable: bool


@dataclass
class ParsedCsv:
    columns: list[Column]
    rows: list[list]
    warnings: list[str] = field(default_factory=list)


def display_name(filename: str) -> str:
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:80] or "Uploaded data"


def _decode(data: bytes) -> str:
    if b"\x00" in data:
        raise CsvImportError("This file contains binary data. Please upload a plain-text CSV file.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvImportError("The file's text encoding wasn't recognised. Save it as a UTF-8 CSV and try again.")


def _row_widths(text: str, delimiter: str, limit: int = 20) -> list[int] | None:
    widths = []
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True):
            if any(cell.strip() for cell in row):
                widths.append(len(row))
                if len(widths) >= limit:
                    break
    except csv.Error:
        return None
    return widths


def _delimiter(text: str) -> str:
    """Comma unless another delimiter splits the first rows into a consistent, larger number of columns.

    csv.Sniffer alone guesses wrongly when values contain the other delimiter, so its guess is checked.
    """
    try:
        sniffed = csv.Sniffer().sniff(text[:20000], delimiters=_DELIMITERS).delimiter
    except csv.Error:
        sniffed = ","
    best, best_width = ",", 0
    for candidate in dict.fromkeys([",", sniffed]):
        widths = _row_widths(text, candidate)
        if widths and len(set(widths)) == 1 and widths[0] > best_width:
            best, best_width = candidate, widths[0]
    return best


def _column_names(headers: list[str]) -> list[str]:
    names, used = [], set()
    for index, header in enumerate(headers):
        base = re.sub(r"[^0-9a-z]+", "_", header.strip().lower()).strip("_")[:50].rstrip("_")
        if not base:
            base = f"column_{index + 1}"
        if base[0].isdigit():
            base = f"c_{base}"
        if base in _RESERVED:
            base = f"{base}_"
        name, suffix = base, 1
        while name in used:
            suffix += 1
            name = f"{base}_{suffix}"
        used.add(name)
        names.append(name)
    return names


def _is_date(value: str) -> bool:
    try:
        return bool(_DATE.match(value)) and bool(date.fromisoformat(value))
    except ValueError:
        return False


def _is_timestamp(value: str) -> bool:
    try:
        return bool(_TIMESTAMP.match(value)) and bool(datetime.fromisoformat(value.replace(" ", "T")))
    except ValueError:
        return False


def _is_decimal(value: str) -> bool:
    if not _DECIMAL.match(value):
        return False
    try:
        return abs(Decimal(value).adjusted()) <= 300
    except InvalidOperation:
        return False


def _infer(values: list[str]) -> tuple[str, list]:
    """Pick the narrowest type every non-empty value fits; anything doubtful stays text."""
    present = [value for value in values if value]
    if not present or any(_LEADING_ZERO.match(value) for value in present):
        kind = "text"  # leading zeros (IDs, postcodes) would be lost as numbers
    elif all(_INTEGER.match(value) and -_BIGINT <= int(value) < _BIGINT for value in present):
        kind = "integer"
    elif all(_is_decimal(value) for value in present):
        kind = "decimal"
    elif all(value.lower() in _BOOLEANS for value in present):
        kind = "boolean"
    elif all(_is_date(value) for value in present):
        kind = "date"
    elif all(_is_timestamp(value) for value in present):
        kind = "timestamp"
    else:
        kind = "text"
    convert = {"integer": int, "decimal": Decimal, "boolean": lambda v: _BOOLEANS[v.lower()],
               "date": date.fromisoformat, "timestamp": lambda v: datetime.fromisoformat(v.replace(" ", "T")),
               "text": str}[kind]
    return kind, [convert(value) if value else None for value in values]


def parse_csv(data: bytes) -> ParsedCsv:
    if not data.strip():
        raise CsvImportError("The file is empty.")
    if len(data) > settings.upload_max_bytes:
        raise CsvImportError(f"The file is larger than {settings.upload_max_bytes / 1024 / 1024:g} MB.")
    text = _decode(data)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(text), strict=True)
    raw_rows: list[list[str]] = []
    try:
        header = next(reader, None)
        if not header or not any(cell.strip() for cell in header):
            raise CsvImportError("The first row must contain the column names.")
        width = len(header)
        if width > settings.upload_max_columns:
            raise CsvImportError(f"The file has {width} columns; the limit is {settings.upload_max_columns}.")
        repeated = [h for h, n in Counter(h.strip().lower() for h in header if h.strip()).items() if n > 1]
        if repeated:
            raise CsvImportError(f"Column names must be unique; repeated: {', '.join(sorted(repeated))[:200]}.")
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != width:
                raise CsvImportError(f"Line {reader.line_num} has {len(row)} values, but the header has {width}.")
            if len(raw_rows) >= settings.upload_max_rows:
                raise CsvImportError(f"The file has more than {settings.upload_max_rows:,} rows.")
            if any(len(cell) > settings.upload_max_cell_chars for cell in row):
                raise CsvImportError(f"Line {reader.line_num} has a value longer than "
                                     f"{settings.upload_max_cell_chars:,} characters.")
            raw_rows.append([cell.strip() for cell in row])
    except csv.Error as exc:
        raise CsvImportError(f"The file isn't valid CSV near line {reader.line_num}. Check its quote marks.") from exc
    if not raw_rows:
        raise CsvImportError("The file has column names but no data rows.")

    names = _column_names(header)
    columns, typed_columns, warnings = [], [], []
    for index, name in enumerate(names):
        kind, typed = _infer([row[index] for row in raw_rows])
        nullable = any(value is None for value in typed)
        columns.append(Column(name=name, label=header[index].strip() or name, type=kind, nullable=nullable))
        typed_columns.append(typed)
        if all(value is None for value in typed):
            warnings.append(f"Column '{columns[-1].label}' is empty.")
    duplicates = len(raw_rows) - len({tuple(row) for row in raw_rows})
    if duplicates:
        warnings.append(f"{duplicates:,} rows are exact copies of other rows; they were kept.")
    return ParsedCsv(columns=columns, rows=[list(row) for row in zip(*typed_columns)], warnings=warnings)
