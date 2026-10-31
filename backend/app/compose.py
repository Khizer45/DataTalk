"""Demo mode, part two: builds SQL for questions made of known pieces (a measure, a breakdown, filters).

A question is answered only when every word in it is understood. Filters are read first ("in 2025", "from
Pakistan", "through the website", "pending orders", "Enterprise customers"); what is left must then match one
question shape exactly, such as "<measure> by <breakdown>" or "which <thing> had the most <measure>". Anything
left over, or a combination that has no single sensible reading, is declined with a reason instead of guessed.

Small typos in known words are corrected ("revenu" → "revenue") and the correction is reported in the
explanation, so the person can see how their question was read.
"""
import re
from dataclasses import dataclass, field

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
_MONTH_ALIASES = {**{name: i for i, name in enumerate(MONTHS, 1)},
                  **{name[:3]: i for i, name in enumerate(MONTHS, 1)}, "sept": 9}
DATA_YEARS = (2024, 2025)

COUNTRIES = {"united states": "United States", "usa": "United States", "united kingdom": "United Kingdom",
             "uk": "United Kingdom", "pakistan": "Pakistan", "canada": "Canada", "germany": "Germany",
             "india": "India", "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates",
             "australia": "Australia"}
SEGMENTS = {"consumer": "Consumer", "small business": "Small business", "enterprise": "Enterprise"}
CHANNELS = {"website": "Website", "web": "Website", "mobile app": "Mobile app", "app": "Mobile app",
            "mobile": "Mobile app", "partner": "Partner", "partners": "Partner"}
STATUSES = {"pending": "pending", "cancelled": "cancelled", "canceled": "cancelled", "completed": "completed",
            "complete": "completed"}
CATEGORIES = {"software": "Software", "add on": "Add-on", "add ons": "Add-on", "addon": "Add-on",
              "addons": "Add-on", "service": "Service", "services": "Service"}


class Decline(ValueError):
    """The question was understood well enough to say why it cannot be answered."""


def _alt(words) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


# ---------------------------------------------------------------- vocabulary pieces

MEASURES = {
    "aov": r"average order (?:value|size|amount)|aov|average (?:revenue|spend|amount) per order",
    "signups": r"new customers|new customer signups|signups|sign ups|customer signups",
    "buyers": r"(?:different|distinct|unique) (?:customers|buyers)|buyers",
    "quantity": r"(?:total )?(?:quantity|units|items) sold|(?:total )?(?:quantity|units)",
    "revenue": r"(?:total )?(?:revenue|sales|income|earnings|spend|spending|money spent|amount spent)",
    "orders": r"(?:total )?(?:number of orders|order count|orders count|count of orders|orders)",
    "customers": r"(?:total )?(?:number of customers|customer count|count of customers|customers)",
    "products": r"(?:total )?(?:number of products|product count|products)",
}
DIMENSIONS = {
    "month": r"(?:calendar )?months?",
    "year": r"years?",
    "country": r"(?:customer )?(?:country|countries)",
    "channel": r"(?:sales )?channels?",
    "status": r"(?:order )?status(?:es)?",
    "category": r"(?:product )?categor(?:y|ies)",
    "segment": r"(?:customer )?segments?",
    "product": r"products?",
    "customer": r"customers?",
}
_M = "(?P<m>" + "|".join(MEASURES.values()) + ")"
_D = "(?P<d>" + "|".join(DIMENSIONS.values()) + ")"
_N = r"(?:(?P<n>\d{1,3}) )?"
_DIR = r"(?P<dir>most|highest|largest|biggest|greatest|best|least|lowest|fewest|smallest|worst)"
_BY = r"(?:by|per|for each|in each|for every|across|broken down by|split by|grouped by|by each)"
_TAIL = r"(?: (?:for|from|of|on|across|in) (?:all |the )?orders)?(?: (?:overall|in total|so far|altogether))?"
_HOW_MANY_VERB = (r"(?:are there|were there|do we have|did we have|have we had|did we get|did we receive|have we got"
                  r"|were placed|were made|have been placed|were received|came in|came|are in the catalog(?:ue)?"
                  r"|in the catalog(?:ue)?|are listed|do we sell|exist|are|were|in total|total"
                  r"|(?P<signup>signed up|joined|have signed up|have joined|registered)"
                  r"|(?P<bought>(?:placed|made|have placed|have made) (?:at least one |an |any )?orders?|ordered))")

_LEADIN = re.compile(r"^(?:please|can you|could you|show me|show|list|give me|tell me|get|find|display|"
                     r"what is|what are|what was|what were|who are|who is|who were|who was|i want|i need|"
                     r"i want to see|see|calculate|compute|compare|me|the|our|all|of) ")

_WORDS = """
how many much what which who is are was were the a an of in on for by per each every from through via using with
and to that have has had do did does we our there show list give tell me get find display total number count sum
average order orders value size amount revenue sales income earnings spend spent spending money customers customer
products product month months year years country countries channel channels status statuses category categories
segment segments top best biggest largest highest most least lowest fewest smallest worst greatest leading bottom
quantity units items sold placed made generated earned brought produced received came signed joined registered
new signups buyers different distinct unique pending cancelled canceled completed complete website mobile app
partner partners consumer small business enterprise software service services catalog catalogue overall so far
calendar sales channel compare please could can you see calculate compute recent latest newest names name their
expensive cheapest cost costs price prices priced more less than over under dollars never been ordered shipped
date dates yet not without missing
""".split()
_VOCAB = set(_WORDS) | {w for words in (COUNTRIES, SEGMENTS, CHANNELS, STATUSES, CATEGORIES, _MONTH_ALIASES)
                        for key in words for w in key.split()}


# ---------------------------------------------------------------- text clean-up

def _edit_distance(a: str, b: str, limit: int) -> int:
    """Damerau-Levenshtein distance (adjacent swaps count as one), stopping early past limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def correct_typos(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Replace unknown words that are one small slip away from exactly one known word."""
    corrections, words = [], []
    for word in text.split():
        if word in _VOCAB or not word.isalpha() or len(word) < 4:
            words.append(word)
            continue
        limit = 1 if len(word) < 8 else 2
        scored = sorted((_edit_distance(word, known, limit), known) for known in _VOCAB if len(known) >= 3)
        best = [known for distance, known in scored if distance == scored[0][0] and distance <= limit]
        if len(best) == 1:
            corrections.append((word, best[0]))
            words.append(best[0])
        else:
            words.append(word)
    return " ".join(words), corrections


def clean(question: str) -> str:
    q = question.casefold().replace("’", "'")
    q = re.sub(r"\bwhat's\b", "what is", q)
    q = re.sub(r"\bwho's\b", "who is", q)
    q = re.sub(r"'s\b", "", q)
    q = re.sub(r"(?<=\w)-(?=\w)", " ", q)  # add-on, best-selling
    q = re.sub(r"[^\w$ ]+", " ", q)
    return " ".join(q.split())


# ---------------------------------------------------------------- filters

@dataclass
class Filters:
    year: int | None = None
    month: int | None = None
    country: str | None = None
    segment: str | None = None
    channel: str | None = None
    status: str | None = None
    category: str | None = None
    notes: list[str] = field(default_factory=list)

    def set(self, name: str, value):
        current = getattr(self, name)
        if current is not None and current != value:
            raise Decline(f"The question names two different {name} values; demo mode can only filter on one.")
        setattr(self, name, value)


def _year(text: str) -> int:
    year = int(text)
    if year not in DATA_YEARS:
        raise Decline("The demo data only covers January 2024 to December 2025, so there is nothing to show for "
                      f"{year}.")
    return year


def extract_filters(q: str) -> tuple[str, Filters]:
    f = Filters()
    months = _alt(_MONTH_ALIASES)
    steps = [
        # "between 2024 and 2025", "in 2024 and 2025" → a breakdown by year, not a filter
        (r"\b(?:in|for|between|across) 2024 and 2025\b", lambda m: "by year"),
        (rf"\b(?:in|during|for|of) (?P<mo>{months}) (?:of )?(?P<y>\d{{4}})\b",
         lambda m: (f.set("month", _MONTH_ALIASES[m["mo"]]), f.set("year", _year(m["y"])), "")[-1]),
        (r"\b(?:in|during|for|of) (?:the year )?(?P<y>\d{4})\b", lambda m: (f.set("year", _year(m["y"])), "")[-1]),
        (rf"\b(?:who are |that are |which are )?(?:based in|located in|from|in) (?:the )?(?P<c>{_alt(COUNTRIES)})\b",
         lambda m: (f.set("country", COUNTRIES[m["c"]]), "")[-1]),
        (rf"\b(?:(?:that|which) )?(?:were |was |are )?(?:placed |made |came |coming |received )?"
         rf"(?:through|via|on|from|using|in|for) (?:the |our )?(?P<ch>{_alt(CHANNELS)})(?: channel)?\b",
         lambda m: (f.set("channel", CHANNELS[m["ch"]]), "")[-1]),
        (rf"\b(?P<ch>{_alt(CHANNELS)}) orders\b", lambda m: (f.set("channel", CHANNELS[m["ch"]]), "orders")[-1]),
        (rf"\b(?:from|among|for|by|of) (?:the |our )?(?P<s>{_alt(SEGMENTS)}) customers\b",
         lambda m: (f.set("segment", SEGMENTS[m["s"]]), "")[-1]),
        (rf"\b(?P<s>{_alt(SEGMENTS)}) (?P<who>customers?)\b",
         lambda m: (f.set("segment", SEGMENTS[m["s"]]), m["who"])[-1]),
        (rf"\b(?:in|from|for) the (?P<cat>{_alt(CATEGORIES)}) category\b",
         lambda m: (f.set("category", CATEGORIES[m["cat"]]), "")[-1]),
        (rf"\b(?:from|of|for|on|in) (?:the |our |all )?(?P<cat>{_alt(CATEGORIES)}) products\b",
         lambda m: (f.set("category", CATEGORIES[m["cat"]]), "")[-1]),
        (rf"\b(?P<cat>{_alt(CATEGORIES)}) products\b", lambda m: (f.set("category", CATEGORIES[m["cat"]]), "products")[-1]),
        (rf"\b(?:counting|using|from|for|of|on|in|across) (?:only )?(?:the )?(?P<st>{_alt(STATUSES)}) orders(?: only)?\b",
         lambda m: (f.set("status", STATUSES[m["st"]]), "")[-1]),
        (rf"\b(?P<st>{_alt(STATUSES)}) orders\b", lambda m: (f.set("status", STATUSES[m["st"]]), "orders")[-1]),
        (rf"\borders (?:that |which )?(?:are|were|have been|got|is|was) (?P<st>{_alt(STATUSES)})\b",
         lambda m: (f.set("status", STATUSES[m["st"]]), "orders")[-1]),
    ]
    for pattern, replace in steps:
        q = re.sub(pattern, replace, q)
        q = " ".join(q.split())
    return q, f


# ---------------------------------------------------------------- question shapes

@dataclass
class Spec:
    measure: str
    dimension: str | None = None
    limit: int | None = None
    ascending: bool = False
    bare: bool = False  # just a vague word such as "sales", with nothing else


def _kind(table: dict, text: str) -> str:
    return next(key for key, pattern in table.items() if re.fullmatch(pattern, text))


def _plural(text: str) -> bool:
    return text.endswith("s") and not text.endswith("status")


def _top(m: re.Match, ascending: bool, measure: str | None = None) -> Spec:
    d = _kind(DIMENSIONS, m["d"])
    n = int(m["n"]) if m["n"] else (5 if _plural(m["d"]) else 1)
    return Spec(measure or _kind(MEASURES, m["m"]), d, max(1, min(n, 100)), ascending)


_LOW = {"least", "lowest", "fewest", "smallest", "worst"}


def _how_many(m: re.Match) -> str:
    e = m["e"]
    if e == "customers" and m["signup"]:
        return "signups"
    if e == "customers" and (m["bought"] or m["distinct"]):
        return "buyers" if m["bought"] else "customers"
    if m["signup"] or m["bought"]:
        raise Decline("Demo mode didn't understand which count you meant.")
    return {"new customers": "signups"}.get(e, e)


_E = r"(?:(?P<distinct>different|distinct|unique) )?(?P<e>new customers|orders|customers|products)"
SHAPES = [
    (rf"how many {_E}(?: {_HOW_MANY_VERB})?{_TAIL}", lambda m: Spec(_how_many(m))),
    (rf"how many {_E}(?: {_HOW_MANY_VERB})? (?:in|by|per|for|from|across|with|of) each {_D}{_TAIL}",
     lambda m: Spec(_how_many(m), _kind(DIMENSIONS, m["d"]))),
    (rf"how many {_E} (?:does|did|has|have) each {_D} (?:have|had|placed|place|made|make|got|get)",
     lambda m: Spec(_how_many(m), _kind(DIMENSIONS, m["d"]))),
    (rf"how many {_E}(?: {_HOW_MANY_VERB})? (?:in|for|of) each {_D} of the year",
     lambda m: Spec(_how_many(m), _kind(DIMENSIONS, m["d"]))),
    (rf"(?:how much )?{_M}{_TAIL}",
     lambda m: Spec(_kind(MEASURES, m["m"]), bare=m["m"] in ("sales", "spend", "spending", "income", "earnings"))),
    (rf"how much {_M} (?:did we (?:make|get|earn|generate|have)|have we made|was (?:made|generated|earned))",
     lambda m: Spec(_kind(MEASURES, m["m"]))),
    (rf"{_M}{_TAIL} {_BY} {_D}{_TAIL}", lambda m: Spec(_kind(MEASURES, m["m"]), _kind(DIMENSIONS, m["d"]))),
    (rf"{_M} (?:in|for|of) each {_D} of the year", lambda m: Spec(_kind(MEASURES, m["m"]), _kind(DIMENSIONS, m["d"]))),
    (rf"{_D} by {_M}", lambda m: Spec(_kind(MEASURES, m["m"]), _kind(DIMENSIONS, m["d"]))),
    (rf"(?:the )?(?:top|best|biggest|largest|leading|highest) {_N}{_D} (?:by|for|in terms of|based on|ranked by) {_M}{_TAIL}",
     lambda m: _top(m, False)),
    (rf"(?:the )?(?:bottom|worst|lowest|smallest) {_N}{_D} (?:by|for|in terms of|based on|ranked by) {_M}{_TAIL}",
     lambda m: _top(m, True)),
    (rf"(?:which|what) {_N}{_D}(?: (?:has|had|have|generated|generate|generates|made|earned|brought in|produced|got"
     rf"|received|saw|did|does|is|was|are|were|sold|sells|sell|had the))? (?:the )?{_DIR} (?:in )?{_M}{_TAIL}",
     lambda m: _top(m, m["dir"] in _LOW)),
    (rf"(?:which|what|who) {_N}{_D} (?P<verb>spent|spend|spends|placed|place|places|ordered|bought) (?:the )?{_DIR}(?: {_M})?{_TAIL}",
     lambda m: _top(m, m["dir"] in _LOW, None if m["m"] else ("revenue" if m["verb"].startswith("spen") else "orders"))),
    (rf"{_N}{_D} with (?:the )?{_DIR} {_M}{_TAIL}", lambda m: _top(m, m["dir"] in _LOW)),
]
SHAPES = [(re.compile(pattern), build) for pattern, build in SHAPES]


def match_shape(q: str) -> Spec | None:
    for _ in range(4):  # "show me the ..." → strip lead-in words one at a time
        for pattern, build in SHAPES:
            m = pattern.fullmatch(q)
            if m:
                return build(m)
        stripped = _LEADIN.sub("", q, count=1)
        if stripped == q:
            break
        q = stripped
    return None


# ---------------------------------------------------------------- SQL

_ORDER_MEASURES = {"revenue", "orders", "aov", "buyers", "quantity"}
_COMPLETED_ONLY = {"revenue", "aov", "quantity"}
_MEASURE_NAMES = {"revenue": "revenue", "orders": "orders", "aov": "average_order_value", "buyers": "customers",
                  "quantity": "quantity", "customers": "customers", "signups": "new_customers", "products": "products"}
_DIM_LABEL = {"month": "month", "year": "year", "country": "customer country", "channel": "sales channel",
              "status": "order status", "category": "product category", "segment": "customer segment",
              "product": "product", "customer": "customer"}


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _date_range(column: str, f: Filters) -> list[str]:
    if f.year is None:
        return []
    if f.month is None:
        return [f"{column} >= '{f.year}-01-01'", f"{column} < '{f.year + 1}-01-01'"]
    end_year, end_month = (f.year + 1, 1) if f.month == 12 else (f.year, f.month + 1)
    return [f"{column} >= '{f.year}-{f.month:02d}-01'", f"{column} < '{end_year}-{end_month:02d}-01'"]


def _period(f: Filters) -> str:
    if f.year is None:
        return ""
    return f"{MONTHS[f.month - 1].capitalize()} {f.year}" if f.month else str(f.year)


def build_sql(spec: Spec, f: Filters) -> tuple[str, str]:
    m, d = spec.measure, spec.dimension
    if d == "customer" and m in ("customers", "signups", "buyers"):
        raise Decline("Counting customers per customer doesn't give a useful answer. Try a breakdown by country "
                      "or segment.")
    if m in ("customers", "signups") and (f.channel or f.status or f.category):
        raise Decline("Customers don't have a channel, order status or product category of their own. Try asking "
                      "how many different customers placed orders instead.")
    if m == "customers" and f.year is not None:
        raise Decline("Did you mean customers who signed up in that period, or customers who placed orders in it? "
                      "Ask “How many customers signed up in 2025?” or “How many different customers placed "
                      "orders in 2025?”.")
    if m in ("customers", "signups") and d not in (None, "country", "segment", "month", "year"):
        raise Decline("Demo mode can break customer counts down by country, segment, month or year.")
    if m == "customers" and d in ("month", "year"):
        raise Decline("Did you mean new customers (sign-ups) per period? Ask “How many customers signed up in "
                      "each month?”.")
    if m == "products" and (d not in (None, "category") or any((f.year, f.country, f.segment, f.channel, f.status))):
        raise Decline("Demo mode can count products overall or per category. For sales per product, ask about "
                      "revenue or quantity sold by product.")
    if m in _COMPLETED_ONLY and f.status not in (None, "completed"):
        raise Decline("Revenue, average order value and quantity sold only count completed orders, so they can't "
                      f"be filtered to {f.status} orders.")
    if m in _COMPLETED_ONLY and d == "status":
        raise Decline("Revenue only counts completed orders, so it can't be broken down by order status. Try "
                      "“How many orders are in each status?”.")
    if m == "aov" and (d in ("product", "category", "customer") or f.category):
        raise Decline("Average order value is per order, so it can't be split by product or category, and "
                      "per-customer averages aren't supported in demo mode.")
    if d == "customer" and (m == "quantity" or f.category):
        raise Decline("Demo mode can't combine customers with quantities or product categories yet.")

    name = _MEASURE_NAMES[m]
    items = d in ("product", "category") or m == "quantity" or f.category is not None
    if m in ("customers", "signups", "products"):
        return _entity_count(spec, f, name)
    if d == "customer":
        return _per_customer(spec, f, name)
    if d == "product" and spec.ascending:
        return _per_product_with_zero(spec, f, name)

    where = ["o.status = 'completed'"] if m in _COMPLETED_ONLY else []
    if f.status and m not in _COMPLETED_ONLY:
        where.append(f"o.status = {_q(f.status)}")
    where += _date_range("o.order_date", f)
    if f.channel:
        where.append(f"o.channel = {_q(f.channel)}")
    if f.country:
        where.append(f"c.country = {_q(f.country)}")
    if f.segment:
        where.append(f"c.segment = {_q(f.segment)}")
    if f.category:
        where.append(f"p.category = {_q(f.category)}")

    value = {"revenue": "SUM(oi.quantity * oi.unit_price)" if items else "SUM(o.total_amount)",
             "orders": "COUNT(DISTINCT o.id)" if items else "COUNT(*)",
             "aov": "ROUND(AVG(o.total_amount), 2)", "buyers": "COUNT(DISTINCT o.customer_id)",
             "quantity": "SUM(oi.quantity)"}[m]
    label = {"month": "to_char(date_trunc('month', o.order_date), 'YYYY-MM') AS month",
             "year": "to_char(o.order_date, 'YYYY') AS year", "country": "c.country", "segment": "c.segment",
             "channel": "o.channel", "status": "o.status", "category": "p.category", "product": "p.name AS product",
             None: None}[d]
    tables = "order_items oi JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id" if items \
        else "orders o"
    if f.country or f.segment or d in ("country", "segment"):
        tables += " JOIN customers c ON c.id = o.customer_id"
    sql = f"SELECT {label + ', ' if label else ''}{value} AS {name} FROM {tables}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if label:
        sql += " GROUP BY 1"
        if spec.limit is not None:
            sql += f" ORDER BY {name} {'ASC' if spec.ascending else 'DESC'}, 1 LIMIT {spec.limit}"
        else:
            sql += " ORDER BY 1" if d in ("month", "year") else f" ORDER BY {name} DESC"
    return sql, _explain(spec, f)


def _entity_count(spec: Spec, f: Filters, name: str) -> tuple[str, str]:
    m, d = spec.measure, spec.dimension
    if m == "products":
        sql = "SELECT " + ("p.category, " if d else "") + f"COUNT(*) AS {name} FROM products p"
        if f.category:
            sql += f" WHERE p.category = {_q(f.category)}"
    else:
        where = []
        if f.country:
            where.append(f"c.country = {_q(f.country)}")
        if f.segment:
            where.append(f"c.segment = {_q(f.segment)}")
        where += _date_range("c.created_at", f)
        label = {"country": "c.country", "segment": "c.segment",
                 "month": "to_char(date_trunc('month', c.created_at), 'YYYY-MM') AS month",
                 "year": "to_char(c.created_at, 'YYYY') AS year", None: None}[d]
        sql = f"SELECT {label + ', ' if label else ''}COUNT(*) AS {name} FROM customers c"
        if where:
            sql += " WHERE " + " AND ".join(where)
    if d:
        if spec.limit is not None:
            sql += f" GROUP BY 1 ORDER BY {name} {'ASC' if spec.ascending else 'DESC'}, 1 LIMIT {spec.limit}"
        else:
            sql += " GROUP BY 1 ORDER BY 1" if d in ("month", "year") else f" GROUP BY 1 ORDER BY {name} DESC"
    return sql, _explain(spec, f)


def _per_customer(spec: Spec, f: Filters, name: str) -> tuple[str, str]:
    """Customers with no matching orders count as 0, so they appear in full lists and at the bottom of rankings."""
    on = ["o.customer_id = c.id"]
    if spec.measure in _COMPLETED_ONLY:
        on.append("o.status = 'completed'")
    elif f.status:
        on.append(f"o.status = {_q(f.status)}")
    on += _date_range("o.order_date", f)
    if f.channel:
        on.append(f"o.channel = {_q(f.channel)}")
    value = {"revenue": "COALESCE(SUM(o.total_amount), 0)", "orders": "COUNT(o.id)"}[spec.measure]
    sql = f"SELECT c.name AS customer, {value} AS {name} FROM customers c LEFT JOIN orders o ON " + " AND ".join(on)
    where = [f"c.country = {_q(f.country)}"] if f.country else []
    if f.segment:
        where.append(f"c.segment = {_q(f.segment)}")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" GROUP BY c.id, c.name ORDER BY {name} {'ASC' if spec.ascending else 'DESC'}, c.name"
    if spec.limit is not None:
        sql += f" LIMIT {spec.limit}"
    return sql, _explain(spec, f)


def _per_product_with_zero(spec: Spec, f: Filters, name: str) -> tuple[str, str]:
    """Lowest-ranked products: products with no matching sales count as 0 (the Legacy Plan was never ordered)."""
    on = ["o.id = oi.order_id"]
    if spec.measure in _COMPLETED_ONLY:
        on.append("o.status = 'completed'")
    elif f.status:
        on.append(f"o.status = {_q(f.status)}")
    on += _date_range("o.order_date", f)
    if f.channel:
        on.append(f"o.channel = {_q(f.channel)}")
    if f.country or f.segment:
        raise Decline("Demo mode can't rank the lowest-selling products for one country or segment yet.")
    matched = "o.id IS NOT NULL"
    value = {"revenue": f"COALESCE(SUM(CASE WHEN {matched} THEN oi.quantity * oi.unit_price END), 0)",
             "quantity": f"COALESCE(SUM(CASE WHEN {matched} THEN oi.quantity END), 0)",
             "orders": f"COUNT(DISTINCT CASE WHEN {matched} THEN o.id END)"}[spec.measure]
    sql = (f"SELECT p.name AS product, {value} AS {name} FROM products p "
           f"LEFT JOIN order_items oi ON oi.product_id = p.id LEFT JOIN orders o ON " + " AND ".join(on))
    if f.category:
        sql += f" WHERE p.category = {_q(f.category)}"
    sql += f" GROUP BY p.id, p.name ORDER BY {name} ASC, p.name LIMIT {spec.limit}"
    return sql, _explain(spec, f)


_DIM_PLURAL = {"month": "months", "year": "years", "country": "countries", "channel": "sales channels",
               "status": "order statuses", "category": "product categories", "segment": "customer segments",
               "product": "products", "customer": "customers"}


def _explain(spec: Spec, f: Filters) -> str:
    m, d = spec.measure, spec.dimension
    what = {"revenue": "revenue from completed orders",
            "orders": f"{f.status} orders" if f.status else "orders (any status)",
            "aov": "average order value of completed orders", "buyers": "different customers who placed orders",
            "quantity": "units sold in completed orders", "customers": "customers",
            "signups": "customers who signed up", "products": "products"}[m]
    verb = {"revenue": "Totals", "quantity": "Totals", "aov": "Calculates the"}.get(m, "Counts")
    if spec.limit is not None:
        rank = "bottom" if spec.ascending else "top"
        if spec.limit > 1:
            text = f"Finds the {rank} {spec.limit} {_DIM_PLURAL[d]} by {what}"
        else:
            text = f"Finds the {_DIM_LABEL[d]} with the {'lowest' if spec.ascending else 'highest'} {what}"
    elif d:
        text = f"{verb} {what} for each {_DIM_LABEL[d]}"
    else:
        text = f"{verb} {what}"
    conditions = []
    if _period(f):
        conditions.append(f"{'sign-ups' if m == 'signups' else 'orders placed'} in {_period(f)}")
    if f.channel:
        conditions.append(f"the {f.channel} channel")
    if f.country:
        conditions.append(f"customers in {f.country}")
    if f.segment:
        conditions.append(f"{f.segment} customers")
    if f.category:
        conditions.append(f"{f.category} products")
    if conditions:
        text += " — only " + ", ".join(conditions)
    if spec.limit is not None and d in ("customer", "product") and spec.ascending:
        text += " (those with none count as 0)"
    return text + "."


def compose_sql(question: str) -> tuple[str, str]:
    """Return (sql, explanation). Raises Decline with a reason, or LookupError when the question isn't understood."""
    q, corrections = correct_typos(clean(question))
    q, filters = extract_filters(q)
    spec = match_shape(q)
    if spec is None:
        raise LookupError(q)
    if spec.bare and filters == Filters():
        raise Decline("“Sales” on its own could mean several things. Try “What is our total revenue?”, "
                      "“Revenue by month” or “Top 5 products by revenue”.")
    sql, explanation = build_sql(spec, filters)
    if corrections:
        explanation += " Read " + ", ".join(f"“{a}” as “{b}”" for a, b in corrections) + "."
    return sql, explanation
