# DataTalk — ask a database questions in plain English

DataTalk is an open-source, safety-focused text-to-SQL application. You ask questions in plain English about structured data — a synthetic demo database, a CSV file you upload, or a PostgreSQL database the person running DataTalk has connected — and DataTalk writes a PostgreSQL query, checks it, runs it with read-only access, and shows the answer as a table and a simple chart. It focuses on doing this **safely** and **measurably** rather than on covering every kind of question.

It runs in two modes:

- **Demo mode** (no AI key): answers questions about the demo shop that are built from known pieces: a measure (revenue, orders, average order value, quantity sold, customers, products), an optional breakdown (month, year, country, channel, status, category, segment, product, customer) or ranking ("top 3 …", "which … had the most …"), and optional filters (a year or month such as "in March 2025", a country, channel, segment, order status or product category). Small typos are corrected and reported in the explanation. It is deliberately strict: if it does not understand every part of a question it says so, and why, instead of guessing. Uploaded files can be inspected, but questions about them need AI mode.
- **AI mode** (set `LLM_API_KEY` and `LLM_MODEL`): an OpenAI-compatible model writes the SQL, for the demo data, your uploaded file or a connected database. It replies in a fixed JSON format: the SQL with a one-line explanation and the assumptions it made, or, when your question can be read in more than one way, a clarifying question with options to choose from. Everything the model writes still goes through the same checks.

## How a question is answered

```
CSV upload → file checks + type inference → you review names and types → private table in 'uploads' (expires)
database   → configured on the server → read-only role checked → its readable tables in one schema
question   → SQL (AI model or demo rules) → validator (SQLGlot) → read-only PostgreSQL role → table + chart
AI reply   → strict JSON check → SQL + explanation + assumptions, or a clarifying question (nothing runs)
             malformed reply, fixable rejection, unknown column or table → model asked once more (never for a write)
```

## Feature status

| Feature | Status |
| --- | --- |
| Plain-English questions → SQL (AI mode, demo mode) | Implemented |
| SQL validation: single `SELECT`, exposed tables only, allowlisted functions, row cap | Implemented |
| Read-only database role with timeouts, enforced by PostgreSQL | Implemented |
| Results: sortable table, bar chart or a line chart for values over time (when it helps), single-value display, "View the SQL that ran" with a copy button, CSV download of the rows shown | Implemented |
| Dataset page: tables, columns, row counts, column meanings, table links, definitions | Implemented |
| Business context file: column meanings, glossary, verified example queries (`backend/app/business_context.json`) | Implemented; shown on the Dataset page, not yet sent to the AI model |
| Rate limit, concurrency cap, daily cap on AI model calls (a retry counts) | Implemented |
| Evaluation set (62 questions) and runner | Implemented; AI mode not yet measured |
| Structured AI replies (strict JSON), one bounded retry for fixable problems | Implemented; tested with a stand-in model, not yet measured with a real one |
| Clarifying questions with options to choose from; the model's assumptions shown under the results | Implemented (AI mode) |
| Richer AI prompt (types, keys, glossary, examples) | Planned |
| CSV upload: file checks, type inference, private expiring tables, delete | Implemented |
| Review columns before import: rename, change type, read "$1,200", "Rs 1,20,000" or 03/04/2025 correctly | Implemented |
| Questions about uploaded files | Implemented in AI mode (demo-mode rules only know the demo shop) |
| PostgreSQL databases configured on the server (role checked read-only, optional access key) | Implemented in AI mode |
| Typing a database connection string into the browser | Not offered, on purpose (see [docs/SECURITY.md](docs/SECURITY.md)) |
| Saved queries, feedback on answers | Planned |
| User accounts / authentication | Not implemented; uploads are protected by secret per-dataset tokens |

## Demo dataset

A synthetic online software shop, January 2024 to December 2025: 250 customers, 13 products, 2,431 orders and 3,604 order lines. Names are invented and emails use `example.com`. The data is generated from a fixed seed (`backend/app/seed.py`), so every install gets identical rows on any recent Python version.

Definitions used everywhere (demo rules, evaluation, Dataset page) live in `backend/app/business_context.json`, together with what each table and column means and a few verified example queries:

- **Revenue** counts completed orders only; pending and cancelled orders are excluded.
- `shipped_date` is empty (NULL) until an order ships.

The example queries are kept different from the evaluation questions (a test checks this), so they can later be shown to the AI model without inflating its scores.

The data includes cases that test real questions: a product that was never ordered, customers who never ordered, and NULL shipped dates.

## Analyze your own CSV

1. Open **Your data** and choose a `.csv` file (commas, semicolons or tabs; column names in the first row).
2. DataTalk checks the file without storing it and shows a review: your header, the column name used in SQL, the type it will be stored as, and example values. Nothing is saved yet.
3. Fix anything that is wrong: rename a column (clear names help the AI), or pick another type. The type list only offers types that fit **every** value in that column, for example:
   - `$1,200.50`, `Rs 1,20,000`, `(350)` → decimal or whole number (currency signs, thousands or lakh commas, accounting negatives)
   - `03/04/2025` → date as day/month/year or month/day/year (only the orders that fit every value are offered)
   - `yes` / `no`, `Y` / `N`, `1` / `0` → true/false
4. Press **Import**, then **Ask about this file**. The **Ask about** picker always shows which data you are querying.

Good to know:

- Questions about your own file need AI mode. In demo mode you can upload and inspect a file, but questions about it are declined with an explanation.
- Limits (configurable): 5 MB, 50,000 rows, 50 columns. On its own, DataTalk only recognises plain numbers and `YYYY-MM-DD` dates; anything doubtful is kept as text (so leading zeros such as postcodes survive) until you choose otherwise in the review. A comma is never treated as a decimal point.
- The file becomes a private table that is deleted after 24 hours, or when you press **Delete**. The original file is not kept. Only your browser holds the key (a secret token) to the table.
- The AI model sees the table's column names, never its values.

## Connect your own PostgreSQL database (self-hosted)

The person running DataTalk can connect PostgreSQL databases on the server. Visitors pick them from the **Ask about** list; they can never type a connection string into the page (on a public site that would let anyone use your server to reach other machines, and you would be holding other people's passwords).

1. In **your** database, create a role that can only read what DataTalk should see:
   ```sql
   CREATE ROLE datatalk_readonly LOGIN PASSWORD '<long random password>';
   ALTER ROLE datatalk_readonly SET default_transaction_read_only = on;
   GRANT CONNECT ON DATABASE sales TO datatalk_readonly;
   GRANT USAGE ON SCHEMA public TO datatalk_readonly;
   GRANT SELECT ON orders, customers, products TO datatalk_readonly;  -- only the tables to expose
   ```
2. Give the API these environment variables (`sales` is any id you choose):
   ```bash
   DATA_SOURCES=sales
   SOURCE_SALES_URL=postgresql+psycopg://datatalk_readonly:<password>@db.example.com:5432/sales?sslmode=require
   SOURCE_SALES_LABEL=Sales database        # optional: name shown in the app
   SOURCE_SALES_SCHEMA=public               # optional: the one schema questions may use
   SOURCE_SALES_TABLES=orders,customers     # optional: allowlist, narrower than the grants
   SOURCE_SALES_ACCESS_KEY=<long random key> # optional but recommended on a public site
   ```
   In PowerShell, use `$env:DATA_SOURCES = "sales"` and so on before `uvicorn`. With Docker Compose, add them to the `api` service in a `docker-compose.override.yml`; on Render, add them in the service's **Environment** tab.
3. Restart the API. The log says, for each database, whether it is ready, refused (and why) or unreachable. A refused database is never offered.

DataTalk refuses a database role that is a superuser, can create roles or databases, is not read-only by default, belongs to PostgreSQL's file, program or write-all roles, or can create or change tables in the chosen schema. These checks run again whenever the table list refreshes (every 5 minutes). Questions about a database need AI mode, see only the tables its role can read in that one schema, and run read-only with the usual time limit. Without an access key, **anyone who can open your DataTalk can query that database**, so set one (or keep DataTalk private) unless the data is public. Use `sslmode=require` (or `verify-full`) for databases on another machine.

## Safety model

Safety does not depend on the AI model behaving well. Each layer works on its own:

1. **Database roles.** Queries run as a reader role that can only `SELECT`. PostgreSQL itself enforces read-only mode and statement, lock and idle timeouts for that role. The API refuses to start if its role is a superuser or can write. An optional third role imports CSV uploads and can only write to the `uploads` schema; it cannot read the demo tables.
2. **Validator.** SQLGlot parses the SQL. Only one `SELECT` is accepted, only on the exposed tables, using only allowlisted functions. A row cap is added without loosening a smaller `LIMIT`.
3. **Execution.** Each query runs in a read-only transaction that is always rolled back.
4. **API.** Per-client rate limit, a cap on concurrent queries, and a daily cap on AI model calls. Users see plain error messages with a reference ID; details stay in the server log.
5. **Privacy.** Only table and column names (and today's date) are sent to the AI model, never row data. When it is asked again, it also gets the validator's message, or PostgreSQL's message and hint for an unknown column or table, which name columns and tables only.
6. **AI replies.** The model must reply with one of two JSON forms, read strictly. A malformed reply, SQL the validator rejects as fixable, or an unknown column or table gets one retry, checked like any query; a write attempt is never retried, and every model call counts against the daily cap. A clarifying question runs nothing; the option you choose is asked as a new question.
7. **Uploads.** Files are size-, row- and column-limited and parsed strictly; headers become safe, quoted column names; reviewed names are validated, not trusted; values are loaded as data; import is all-or-nothing; each file gets a random private table reachable only with its secret token, and questions about it can only touch that one table.
8. **Connected databases.** Configured on the server only; their role is checked before they are offered and again on every refresh; questions are limited to that database's readable tables in one schema; an optional access key is compared in constant time.

Full details, the tests behind each safeguard, and the known limits: [docs/SECURITY.md](docs/SECURITY.md). Work in progress and next steps: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Evaluation

`backend/eval/cases.json` holds 62 tagged questions about the demo data: 50 that should be answered (filters, aggregates, group-by, dates, NULLs, joins, top-N) and 12 that should be declined (data that doesn't exist, ambiguous questions, requests to change data). Each answerable question has a reference query. An answer passes when its rows match the reference rows; column names, row order (unless it matters) and extra columns are allowed to differ.

```bash
cd backend
python -m app.evaluation                 # writes eval/results/<date>-<mode>.json
```

With Docker (uses the API container's settings, including your AI key if set):

```bash
docker compose run --rm -v ./backend/eval:/app/eval api python -m app.evaluation
```

Measured results:

| Mode | Correct answers (50) | Wrong answers shown | Declined when it should (12) |
| --- | --- | --- | --- |
| Demo mode (rules), 2026-10-27, fixed question types only | 12.0% | 0.0% | 100% |
| Demo mode (rules), 2026-10-31, measures × breakdowns × filters (`eval/results/2026-10-31-rules.json`) | 72.0% | 0.0% | 100% |
| AI mode | not measured yet | | |

The 2026-10-31 demo grammar (`backend/app/compose.py`) was written with the evaluation questions visible, so 72% overstates how often it understands new phrasings; `backend/tests/test_rules.py` checks separate phrasings. The important number is that demo mode shows no wrong answers. "Wrong answers shown" counts results that look like answers but are wrong, which users cannot easily spot. Keep the evaluation questions out of any prompt examples, or the scores stop meaning anything.

Questions go through the same pipeline as the app, including the one retry. In AI mode a clarifying question counts as declining: right for the ambiguous questions, a miss (but never a wrong answer) for answerable ones, reported as "asked to clarify". The report records every model call with the SQL it wrote and why it was not used, so you can see how often the retry was needed. AI mode has not been measured yet: it needs an API key, and no real-model run has been done.

## Run with Docker

Requirements: Docker Compose.

```bash
cp .env.example .env   # then set POSTGRES_PASSWORD, READER_PASSWORD and INGEST_PASSWORD
docker compose up --build
```

Open <http://localhost:5173>. The API is at <http://localhost:8000>; interactive API docs are at <http://localhost:8000/docs>. A one-off `setup` container creates the tables, demo data and reader role before the API starts. The database port is not published.

**Upgrading from an earlier version:** add `INGEST_PASSWORD` to your `.env` (needed for CSV uploads; Compose tells you if it is missing). If you come from a version before the larger demo dataset, the demo schema also changed: if setup reports "older DataTalk demo schema", reset the local database with `docker compose down -v`, then `docker compose up --build`.

To enable AI mode, set `LLM_API_KEY` and `LLM_MODEL` in `.env`. `LLM_BASE_URL` defaults to the OpenAI-compatible `/v1` API base.

## Run locally without Docker

Use Python 3.11+, Node.js 20+ and a PostgreSQL 16 database whose owner role can create roles.

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
export ADMIN_DATABASE_URL=postgresql+psycopg://<owner>:<password>@localhost:5432/datatalk
export DATABASE_URL=postgresql+psycopg://datatalk_reader:<reader-password>@localhost:5432/datatalk
export INGEST_DATABASE_URL=postgresql+psycopg://datatalk_ingest:<ingest-password>@localhost:5432/datatalk  # optional: CSV uploads
python -m app.setup_db          # once; safe to re-run
unset ADMIN_DATABASE_URL
uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` requests to `http://localhost:8000`.

The same backend steps in Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:ADMIN_DATABASE_URL = "postgresql+psycopg://<owner>:<password>@localhost:5432/datatalk"
$env:DATABASE_URL = "postgresql+psycopg://datatalk_reader:<reader-password>@localhost:5432/datatalk"
$env:INGEST_DATABASE_URL = "postgresql+psycopg://datatalk_ingest:<ingest-password>@localhost:5432/datatalk"
python -m app.setup_db
Remove-Item Env:ADMIN_DATABASE_URL
uvicorn app.main:app --reload
```

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest                                   # unit tests; PostgreSQL tests are skipped
```

Frontend unit tests (CSV export, sorting, chart choice) use Node's built-in test runner:

```bash
cd frontend
npm test
```

To include the PostgreSQL tests, point them at a database you can throw away (setup creates tables, data and a reader role there):

```bash
TEST_ADMIN_DATABASE_URL=postgresql+psycopg://<owner>:<password>@localhost:5432/datatalk_test \
TEST_DATABASE_URL=postgresql+psycopg://datatalk_test_reader:<password>@localhost:5432/datatalk_test \
TEST_INGEST_DATABASE_URL=postgresql+psycopg://datatalk_test_ingest:<password>@localhost:5432/datatalk_test \
pytest                                   # the last variable enables the CSV-upload tests
```

## Deploy (Neon + Render + Vercel)

1. **Neon:** create a project and database. From your machine, run setup once with Neon's owner connection string:
   ```bash
   cd backend
   ADMIN_DATABASE_URL="postgresql+psycopg://<neon-owner>:<password>@<neon-host>/<db>?sslmode=require" \
   DATABASE_URL="postgresql+psycopg://datatalk_reader:<new-reader-password>@<neon-host>/<db>?sslmode=require" \
   INGEST_DATABASE_URL="postgresql+psycopg://datatalk_ingest:<new-ingest-password>@<neon-host>/<db>?sslmode=require" \
   python -m app.setup_db
   ```
   Leave out `INGEST_DATABASE_URL` if you don't want CSV uploads on the public demo.
2. **Render:** create a Blueprint from this repo (`render.yaml`). Set `DATABASE_URL` to the **reader** URL above (never the owner URL), optionally `INGEST_DATABASE_URL` to the ingest URL, and leave `CORS_ORIGINS` for step 4. Keep `MAX_ACTIVE_DATASETS` small on a free database plan.
3. **Vercel:** import the repo with **Root Directory** `frontend` and set `VITE_API_BASE_URL` to the Render URL (e.g. `https://datatalk-api.onrender.com`).
4. **Render again:** set `CORS_ORIGINS` to the Vercel URL and redeploy.

Free tiers sleep when idle; the page shows a "waking up" notice while the API starts. If you add an AI key, also set a spending limit with the provider: `LLM_DAILY_LIMIT` is kept in memory and resets when the server restarts. Vercel's free Hobby plan is for non-commercial use, so don't use it for a page that sells services.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | — | Reader role connection (required by the API) |
| `ADMIN_DATABASE_URL` | — | Owner connection, only for `python -m app.setup_db` |
| `EXPOSED_TABLES` | the four demo tables | Tables the reader may query and the model may see |
| `BUSINESS_CONTEXT_PATH` | `backend/app/business_context.json` | Table and column meanings, glossary and verified examples |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | OpenAI base URL, empty, empty | AI mode settings |
| `LLM_DAILY_LIMIT` | `200` | AI model calls per UTC day, retries included, before falling back to demo mode (`0` = no cap) |
| `MAX_RESULT_ROWS` | `200` | Maximum rows returned per question |
| `STATEMENT_TIMEOUT_MS` | `5000` | Query time limit |
| `CORS_ORIGINS` | localhost:5173 | Frontend origins allowed to call the API |
| `RATE_LIMIT_PER_MINUTE` | `20` | Questions per client per minute (`0` = no limit) |
| `MAX_CONCURRENT_QUERIES` | `4` | Queries running at once |
| `INGEST_DATABASE_URL` | — | Ingest role connection; enables CSV uploads (role limited to the `uploads` schema) |
| `UPLOAD_MAX_BYTES`, `UPLOAD_MAX_ROWS`, `UPLOAD_MAX_COLUMNS`, `UPLOAD_MAX_CELL_CHARS` | 5 MB, 50,000, 50, 1,000 | Upload limits |
| `DATASET_TTL_HOURS` | `24` | Hours before an uploaded dataset is deleted |
| `MAX_ACTIVE_DATASETS` | `100` | Uploaded datasets kept at once (protects database storage) |
| `UPLOAD_RATE_LIMIT_PER_MINUTE` | `5` | Uploads per client per minute (`0` = no limit); file checks (previews) are limited separately at the same rate |
| `DATA_SOURCES` | — | Ids of PostgreSQL databases to connect (see [Connect your own PostgreSQL database](#connect-your-own-postgresql-database-self-hosted)) |
| `SOURCE_<ID>_URL` | — | Connection for that database, as a dedicated read-only role (required per id) |
| `SOURCE_<ID>_LABEL`, `SOURCE_<ID>_SCHEMA`, `SOURCE_<ID>_TABLES` | id, `public`, all readable | Display name, the one schema used, optional table allowlist |
| `SOURCE_<ID>_ACCESS_KEY` | — | If set, questions about that database need this key (`X-Source-Key`) |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Service and database status |
| `GET` | `/api/schema` | Tables with columns, keys, links, meanings and row counts; dataset description and definitions; sample questions; mode (`demo` or `ai`); upload limits; connected databases (tables hidden for those with an access key) |
| `POST` | `/api/query` | Generate, validate and run a read-only query; the answer lists the model's `assumptions` (AI mode). When the model needs to know which reading of the question was meant, returns 200 with a `clarification` instead and runs nothing. Optional `dataset_id` (with its token in `X-Dataset-Token`) asks about an uploaded file; optional `source_id` (with `X-Source-Key` if needed) asks about a connected database |
| `GET` | `/api/sources/{id}` | A connected database's tables (needs `X-Source-Key` if it has an access key) |
| `POST` | `/api/datasets/preview?name=<file name>` | Check a CSV (raw body) without storing it: columns, inferred types, the types each column could be read as, example values |
| `POST` | `/api/datasets?name=<file name>` | Upload a CSV as the raw request body; optional `X-Dataset-Columns` header with the reviewed `[{"name", "type"}, ...]`; returns the dataset, its columns and its secret `access_token` (shown once) |
| `GET` | `/api/datasets/{id}` | Columns, row count and expiry of an uploaded dataset (needs `X-Dataset-Token`) |
| `DELETE` | `/api/datasets/{id}` | Delete an uploaded dataset now (needs `X-Dataset-Token`) |

Example:

```json
POST /api/query
{
  "question": "Show the top 5 products by revenue",
  "max_rows": 100
}
```

The response has the `sql` that ran, `rows`, `row_count`, `truncated`, `generator` (`llm` or `rules`), `explanation`, `assumptions` (AI mode; empty in demo mode), `clarification` (`null` for an answer), `source` and `dataset_name`. A clarification in AI mode looks like this, with `sql` empty and no rows:

```json
{
  "question": "Who are our top customers?",
  "clarification": {
    "question": "Should customers be ranked by revenue or by number of orders?",
    "options": ["Who are the top 5 customers by revenue?", "Who are the top 5 customers by number of orders?"]
  },
  "sql": null, "rows": [], "row_count": 0, "assumptions": [], "generator": "llm", "source": "demo"
}
```

Each option is a complete question: send it as the next `question` (about the same data) to get the answer.

## Known limitations

- No authentication; public deployments rely on rate limits and the daily AI cap.
- AI mode accuracy has not been measured yet; structured replies, the retry and clarifying questions are tested with a stand-in model only. The AI model still receives table and column names only; the business context is not yet part of its prompt.
- The explanation and assumptions are the model's own description of its SQL and are not checked against it; "View the SQL that ran" shows what actually ran. Whether the model asks for clarification when it should depends on the model.
- Demo mode only answers a few phrasings by design.
- Rate limits and the daily AI cap are in memory, per API instance.
- Questions about uploaded files need AI mode, and AI-mode accuracy on uploaded files has not been measured.
- Uploads have no user accounts: the dataset token lives in the browser; see [docs/SECURITY.md](docs/SECURITY.md) for the full list of limits.
- Databases are connected by whoever runs DataTalk, through environment variables; visitors cannot connect their own from the browser. Without an access key, a connected database can be queried by anyone who can open the app. AI-mode accuracy on connected databases has not been measured.
- Column review cannot split or merge columns, and only reads `.` as the decimal point (`1.234,5` stays text).
- CSV download contains the rows shown on screen (at most 100 per question), not the full table.
