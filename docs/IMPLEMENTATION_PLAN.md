# DataTalk — implementation plan

This plan tracks the work to turn DataTalk from a demo-only tool into one that also analyzes a
user's own data, starting with CSV upload. It is updated as work lands.

## Baseline (already on `main`)

The repository was audited and hardened over earlier milestones. What exists and is tested:

- **Query pipeline:** question → SQL (AI model via an OpenAI-compatible API, or demo rules) →
  SQLGlot validation (single `SELECT`, allowlisted tables and functions, row cap) → read-only
  PostgreSQL role with statement/idle/lock timeouts → table, chart and single-value result.
- **Two database roles:** an owner used once by `python -m app.setup_db`, and a read-only reader
  used for every query. The API refuses to start if its role is a superuser or can write.
- **Demo dataset:** a fixed-seed synthetic online shop (250 customers, 13 products, 2,431 orders).
- **Evaluation:** 62 tagged questions with reference SQL; a runner that compares result sets.
  Measured demo-mode baseline: 12% answered, 0% wrong answers shown, 100% correct declines.
- **Frontend:** honest UI (no mock controls), Dataset page, sortable results table, CSV download,
  "waking up" state for free-tier cold starts.
- **Tests + CI:** backend pytest (unit + PostgreSQL integration), frontend unit tests, GitHub
  Actions running both plus a demo-mode safety gate.

Baseline checks at the start of this milestone: backend `pytest` 74 passed; frontend `npm test`
17 passed; `npm run build` succeeds; CI green on `main`.

## Known limit in this working session

The LLM model-client code (prompt construction and the structured-reply parser — roadmap items 6
and 7) cannot be authored in this session; the safety system stops those edits. The existing
AI-mode call in `generator.py` is unchanged and still works. CSV upload below reuses that pipeline
and does **not** require writing new model code, so it is buildable here.

## Milestone: CSV upload (this branch)

Goal: a user uploads a CSV, DataTalk infers its schema into an isolated table, and the user can ask
questions about that specific dataset through the same validated, read-only pipeline as the demo.

### Design

- **Third database role `datatalk_ingest`.** Can `CREATE`/`INSERT`/`DROP` only in a dedicated
  `uploads` schema; it is not read-only and has no rights on the demo tables. It is used only to
  import CSVs and manage the dataset registry. The query reader role stays strictly read-only and
  is granted `SELECT` on each uploaded table when it is created.
- **Isolation.** Each dataset is one table with a random name in the `uploads` schema. Queries
  against an uploaded dataset run with `search_path` set to `uploads` only and the validator
  restricted to that single table, so demo tables and other datasets are unreachable.
- **Ownership without accounts.** Upload returns a public `dataset_id` and a secret `access_token`
  (a high-entropy capability). The server stores only a SHA-256 hash of the token and verifies it
  on every read. This is capability-based access for temporary datasets, documented as such — not
  user authentication.
- **Lifecycle.** Datasets expire (default 24h). Expired tables are dropped and registry rows purged
  on startup and on each upload. Limits on file size, rows, columns and cell length are enforced
  and configurable. Import runs in one transaction, so a failure leaves no table and no registry row.
- **Safety.** Uploaded filenames, headers and cell values are never trusted as SQL. Identifiers are
  generated/sanitized and always quoted; values are inserted as parameters. Headers are sanitized to
  safe column names; the original header is kept as a label for display and (later) for the model.

### Acceptance criteria

- Upload a valid CSV → inspect inferred schema → ask a question → get a result from the uploaded
  data. Works for at least two different schemas (not assuming shop data).
- Invalid uploads (wrong type, too big, empty, malformed, duplicate headers) are rejected clearly.
- A dataset cannot be read with the wrong token; expired datasets are gone.
- The read-only reader cannot write to `uploads`; the ingest role cannot read demo tables.
- Demo mode continues to work unchanged.

### Status

- [x] CSV parse / validate / type inference — `backend/app/csv_import.py`, 20 unit tests
  (`tests/test_csv_import.py`), including hostile headers/values, encodings, delimiters and every rejection path.
- [x] Ingest role, uploads schema, capability ownership, cleanup — `backend/app/datasets.py`, `setup_db.py`.
- [x] API endpoints and query-pipeline wiring — `POST/GET/DELETE /api/datasets`, `dataset_id` on `/api/query`,
  `search_path` pinned to `uploads`; demo rules never used for uploads.
- [x] Upload UI — "Your data" page, "Ask about" picker, column-based suggestions, honest demo-mode messages.
- [x] Tests and docs — 17 PostgreSQL upload tests (`tests/test_uploads_postgres.py`), 2 frontend tests,
  `docs/SECURITY.md`, README; browser-checked in demo mode and in AI mode with a stand-in provider.

### Verified results (this milestone)

- Backend: 111 tests pass with uploads enabled; 94 pass + 17 skipped with uploads off; 77 pass without a DB.
- Frontend: 19 unit tests pass; production build succeeds.
- Demo-mode evaluation unchanged: 12% answered, 0% wrong answers shown, 100% correct declines (62 questions).
- Two different CSV schemas (regional sales; semicolon-separated expenses) answered from their own contents in
  AI mode with a stand-in model; real-model accuracy on uploads is **not measured**.

## Milestone: column review and connected databases

Goal: fix wrongly inferred columns before import, and let whoever runs DataTalk connect their own PostgreSQL
databases safely, without letting visitors supply connection strings.

### Design

- **Preview, then import.** `POST /api/datasets/preview` parses the file and stores nothing. For each column it returns
  the inferred type, every type all values fit (`choices`), and example values. The import repeats the parse with the
  reviewed `[{"name", "type"}]` from `X-Dataset-Columns`; the server validates everything again.
- **Strict inference, lenient choices.** Inference is unchanged. When the uploader picks a type, lenient readers accept
  currency signs and codes, thousands and lakh grouping, accounting negatives, day-first or month-first dates and
  yes/no booleans. A comma is never a decimal point.
- **Server-configured sources.** `DATA_SOURCES` plus `SOURCE_<ID>_*` variables. Role checks (see SECURITY.md) before a
  source is offered and on every refresh; tables from the catalog limited to what the role can read in one schema;
  same validator, `search_path` pinned, read-only execution on the source's own engine; optional access key.

### Status

- [x] Preview endpoint, choices, lenient readers, reviewed import — `csv_import.py`, `main.py`; 52 unit tests in
  `test_csv_import.py` (32 new) and 6 new PostgreSQL tests.
- [x] Sources: config, role checks, table discovery, access key, query wiring — `sources.py`; 16 PostgreSQL tests in
  `test_sources_postgres.py`, including refused roles and bypass attempts.
- [x] Frontend: review step (stacked on phones), databases in the picker and on Your data, key form; plain number
  formatting for user data (it was showing every "price" column in dollars). 7 new unit tests.
- [x] Browser-checked with a stand-in model: reviewed `prices.csv` (Rs amounts, DD/MM/YYYY dates) imported as
  decimal and date and summed correctly; a connected `helpdesk` database answered; an ungranted table refused; a
  locked copy refused a wrong key and answered with the right one; demo still works.

### Verified results (this milestone)

- Backend: 165 tests pass with uploads enabled; 142 pass + 23 skipped without the ingest role; 109 pass + 56 skipped
  without a database.
- Frontend: 24 unit tests pass; production build succeeds.
- Demo-mode evaluation unchanged: 12% answered, 0% wrong answers shown, 100% correct declines.
- Removing the role checks or the SQL validator makes the source tests fail (checked by temporarily disabling each).
- Worst-case preview time at the default 5 MB limit (50 columns, every value distinct): about 2.8 s; import about
  2.2 s (run in a worker thread).

### Remaining for user data

- Measure AI-mode accuracy on uploaded files (needs an API key and an upload evaluation set).
- A per-dataset database role would add a database-level layer between uploads (see SECURITY.md).
- Scheduled cleanup instead of cleanup on upload/start.

## Deferred (not in this milestone)

- Structured AI replies, bounded retry, clarifying questions (items 6–7; blocked this session).
- Visitors connecting their own database from the browser (needs user accounts, encrypted credential storage,
  destination allowlists and TLS verification; server-configured sources cover the self-hosted case).
- User accounts, saved analyses persistence, MCP mode, local models, AWS specifics.
