"""Reading an uploaded CSV safely: limits, header clean-up, cautious type inference and reviewed corrections.

Nothing here touches the database. The filename, headers and cell values are untrusted: headers become safe
column names (the original text is kept as a label), and values are returned as typed Python objects that are
later inserted as parameters, never as SQL text.

Inference is strict, so doubtful columns stay text. Before importing, the uploader can review each column and
rename it or choose another way to read it (for example "$1,200" as a number, or 03/04/2025 as day/month/year);
a choice is only offered when every value in the column fits it.
"""
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import partial

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
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
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


# Lenient readers, used when the uploader picks a type. Inference above stays strict and only picks the plain forms.
_MONEY_PREFIX = re.compile(r"^(?:[$\u20ac\u00a3\u00a5\u20b9]|(?:rs|pkr|inr|usd|eur|gbp)\.?)\s*", re.IGNORECASE)
_MONEY_SUFFIX = re.compile(r"\s*(?:[$\u20ac\u00a3\u00a5\u20b9]|rs\.?|pkr|inr|usd|eur|gbp)$", re.IGNORECASE)
# 1,234,567 (thousands) or 12,34,567 (lakh); the decimal point is always "."
_GROUPED = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?:\.\d+)?$")
_PLAIN_NUMBER = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_WHOLE = re.compile(r"^-?\d+$")
_SIMPLE_NUMBER = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")
_BOOLEAN_WORDS = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False, "t": True,
                  "f": False, "1": True, "0": False}
_DATE_PARTS = {"iso": re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"),
               "dmy": re.compile(r"(?P<d>\d{1,2})(?P<sep>[/.-])(?P<m>\d{1,2})(?P=sep)(?P<y>\d{4})"),
               "mdy": re.compile(r"(?P<m>\d{1,2})(?P<sep>[/.-])(?P<d>\d{1,2})(?P=sep)(?P<y>\d{4})")}
_TIME_PART = re.compile(r"(?:[ T](?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:\.(?P<f>\d{1,6}))?)?)?")


def _number_text(value: str) -> str | None:
    """'$1,200.50', 'Rs 1,20,000', '(350)', '-\u20ac5' -> plain number text, or None if it isn't a number."""
    if _SIMPLE_NUMBER.match(value):  # the common case, without the clean-up below
        return value
    text, negative, sign = value.strip(), False, ""
    if text.startswith("(") and text.endswith(")"):  # accounting style negative
        text, negative = text[1:-1].strip(), True
    if text.startswith(("+", "-")):
        sign, text = text[0], text[1:].lstrip()
    text = _MONEY_PREFIX.sub("", text, count=1)
    if not sign and text.startswith(("+", "-")):  # $-5
        sign, text = text[0], text[1:].lstrip()
    text = _MONEY_SUFFIX.sub("", text, count=1)
    if negative and sign:
        return None
    if "," in text:
        if not _GROUPED.match(text):
            return None
        text = text.replace(",", "")
    if not _PLAIN_NUMBER.match(text):
        return None
    return f"-{text}" if negative or sign == "-" else text


def _read_integer(value: str) -> int:
    number = _number_text(value)
    if number is None or not _WHOLE.match(number) or not -_BIGINT <= int(number) < _BIGINT:
        raise ValueError(value)
    return int(number)


def _read_decimal(value: str) -> Decimal:
    number = _number_text(value)
    if number is None or abs(Decimal(number).adjusted()) > 300:
        raise ValueError(value)
    return Decimal(number)


def _read_boolean(value: str) -> bool:
    if value.lower() not in _BOOLEAN_WORDS:
        raise ValueError(value)
    return _BOOLEAN_WORDS[value.lower()]


def _read_date(order: str, value: str) -> date:
    match = _DATE_PARTS[order].fullmatch(value)
    if not match:
        raise ValueError(value)
    return date(int(match["y"]), int(match["m"]), int(match["d"]))


def _read_timestamp(order: str, value: str) -> datetime:
    match = _DATE_PARTS[order].match(value)
    time = _TIME_PART.fullmatch(value, match.end()) if match else None
    if not time:
        raise ValueError(value)
    return datetime(int(match["y"]), int(match["m"]), int(match["d"]), int(time["h"] or 0), int(time["mi"] or 0),
                    int(time["s"] or 0), int((time["f"] or "0").ljust(6, "0")))


# Ways a column can be read: (stored type, reader, wording for messages). Inferred types use the same keys.
CHOICES = {
    "text": ("text", str, "text"),
    "integer": ("integer", _read_integer, "a whole number"),
    "decimal": ("decimal", _read_decimal, "a decimal number"),
    "boolean": ("boolean", _read_boolean, "true/false"),
    "date": ("date", partial(_read_date, "iso"), "a date (YYYY-MM-DD)"),
    "date_dmy": ("date", partial(_read_date, "dmy"), "a date (DD/MM/YYYY)"),
    "date_mdy": ("date", partial(_read_date, "mdy"), "a date (MM/DD/YYYY)"),
    "timestamp": ("timestamp", partial(_read_timestamp, "iso"), "a date and time (YYYY-MM-DD HH:MM)"),
    "timestamp_dmy": ("timestamp", partial(_read_timestamp, "dmy"), "a date and time (DD/MM/YYYY HH:MM)"),
    "timestamp_mdy": ("timestamp", partial(_read_timestamp, "mdy"), "a date and time (MM/DD/YYYY HH:MM)"),
}


def _infer(values: list[str]) -> str:
    """Pick the narrowest type every non-empty value fits; anything doubtful stays text."""
    present = [value for value in values if value]
    if not present or any(_LEADING_ZERO.match(value) for value in present):
        return "text"  # leading zeros (IDs, postcodes) would be lost as numbers
    if all(_INTEGER.match(value) and -_BIGINT <= int(value) < _BIGINT for value in present):
        return "integer"
    if all(_is_decimal(value) for value in present):
        return "decimal"
    if all(value.lower() in _BOOLEANS for value in present):
        return "boolean"
    if all(_is_date(value) for value in present):
        return "date"
    if all(_is_timestamp(value) for value in present):
        return "timestamp"
    return "text"


def _fits(choice: str, values: set[str]) -> bool:
    reader = CHOICES[choice][1]
    try:
        for value in values:
            reader(value)
    except (ValueError, ArithmeticError):
        return False
    return True


def _choices(values: set[str]) -> list[str]:
    fitting = [choice for choice in CHOICES if _fits(choice, values)]
    # A date-only column also parses as midnight timestamps; offering both would only add noise.
    return [c for c in fitting if not (c.startswith("timestamp") and c.replace("timestamp", "date") in fitting)]


def _short(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return value if len(value) <= 40 else value[:39] + "\u2026"


def _convert(label: str, choice: str, values: list[str], lines: list[int]) -> list:
    reader, cache, converted = CHOICES[choice][1], {}, []
    for value, line in zip(values, lines):
        if not value:
            converted.append(None)
            continue
        if value not in cache:
            try:
                cache[value] = reader(value)
            except (ValueError, ArithmeticError):
                raise CsvImportError(f"Column '{_short(label)}': the value '{_short(value)}' on line {line} can't be "
                                     f"read as {CHOICES[choice][2]}. Choose another type, or fix the file.") from None
        converted.append(cache[value])
    return converted


@dataclass
class _Table:
    header: list[str]
    rows: list[list[str]]
    lines: list[int]


def _read(data: bytes) -> _Table:
    if not data.strip():
        raise CsvImportError("The file is empty.")
    if len(data) > settings.upload_max_bytes:
        raise CsvImportError(f"The file is larger than {settings.upload_max_bytes / 1024 / 1024:g} MB.")
    text = _decode(data)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(text), strict=True)
    raw_rows: list[list[str]] = []
    lines: list[int] = []
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
            lines.append(reader.line_num)
    except csv.Error as exc:
        raise CsvImportError(f"The file isn't valid CSV near line {reader.line_num}. Check its quote marks.") from exc
    if not raw_rows:
        raise CsvImportError("The file has column names but no data rows.")
    return _Table(header=header, rows=raw_rows, lines=lines)


def _warnings(table: _Table, empty_labels: list[str]) -> list[str]:
    warnings = [f"Column '{label}' is empty." for label in empty_labels]
    duplicates = len(table.rows) - len({tuple(row) for row in table.rows})
    if duplicates:
        warnings.append(f"{duplicates:,} rows are exact copies of other rows; they were kept.")
    return warnings


def _checked_overrides(overrides, count: int) -> list[tuple[str, str]]:
    """Validate the reviewer's names and types. The messages are safe to show; values are echoed shortened."""
    if not isinstance(overrides, list) or len(overrides) != count:
        raise CsvImportError("The column settings don't match this file. Please check the file again.")
    checked, used = [], set()
    for item in overrides:
        name = item.get("name") if isinstance(item, dict) else None
        choice = item.get("type") if isinstance(item, dict) else None
        if not isinstance(name, str) or not isinstance(choice, str) or choice not in CHOICES:
            raise CsvImportError("The column settings don't match this file. Please check the file again.")
        name = name.strip()
        if not _NAME.match(name):
            raise CsvImportError(f"'{_short(name)}' isn't a valid column name. Use lowercase letters, digits and _, "
                                 "starting with a letter (at most 63 characters).")
        if name in _RESERVED:
            raise CsvImportError(f"'{name}' is a reserved SQL word. Please choose another column name.")
        if name in used:
            raise CsvImportError(f"Two columns are named '{name}'. Column names must be unique.")
        used.add(name)
        checked.append((name, choice))
    return checked


def preview_csv(data: bytes) -> dict:
    """What an import would create, without storing anything: inferred columns, the types each column could be
    read as, and up to three example values, so the uploader can correct names and types first."""
    table = _read(data)
    columns, empty = [], []
    for index, name in enumerate(_column_names(table.header)):
        values = [row[index] for row in table.rows]
        distinct = set(values) - {""}
        label = table.header[index].strip() or name
        if not distinct:
            empty.append(label)
        samples = list(dict.fromkeys(value for value in values if value))[:3]
        columns.append({"name": name, "label": label, "type": _infer(values), "nullable": "" in values,
                        "choices": _choices(distinct),
                        "samples": [_short(value) for value in samples]})
    return {"row_count": len(table.rows), "columns": columns, "warnings": _warnings(table, empty)}


def parse_csv(data: bytes, overrides: list | None = None) -> ParsedCsv:
    """Read and type the file. overrides, if given, is one {"name", "type"} per column from the review step."""
    table = _read(data)
    names = _column_names(table.header)
    reviewed = _checked_overrides(overrides, len(names)) if overrides is not None else None
    columns, typed_columns, empty = [], [], []
    for index, name in enumerate(names):
        values = [row[index] for row in table.rows]
        label = table.header[index].strip() or name
        name, choice = reviewed[index] if reviewed else (name, _infer(values))
        typed = _convert(label, choice, values, table.lines)
        columns.append(Column(name=name, label=label, type=CHOICES[choice][0],
                              nullable=any(value is None for value in typed)))
        typed_columns.append(typed)
        if all(value is None for value in typed):
            empty.append(label)
    return ParsedCsv(columns=columns, rows=[list(row) for row in zip(*typed_columns)],
                     warnings=_warnings(table, empty))
