# DataTalk — ask a database questions in plain English

DataTalk turns a plain-English question into a PostgreSQL query, checks that query, runs it with read-only access, and shows the answer as a table and a simple chart. It is a portfolio and reference project: it ships with a synthetic demo dataset, and it focuses on doing this **safely** and **measurably** rather than on covering every kind of question.

It runs in two modes:

- **Demo mode** (no AI key): answers a small set of question types, phrased like the suggested questions. It is deliberately strict: if it does not fully recognise a question it says so instead of guessing.
- **AI mode** (set `LLM_API_KEY` and `LLM_MODEL`): an OpenAI-compatible model writes the SQL. Everything the model writes still goes through the same checks.

## How a question is answered

```
question → SQL (AI model or demo rules) → validator (SQLGlot) → read-only PostgreSQL role → table + chart
```

## Feature status

| Feature | Status |
| --- | --- |
| Plain-English questions → SQL (AI mode, demo mode) | Implemented |
| SQL validation: single `SELECT`, exposed tables only, allowlisted functions, row cap | Implemented |
| Read-only database role with timeouts, enforced by PostgreSQL | Implemented |
| Results: sortable table, bar chart (when it helps), single-value display, "View the SQL that ran", CSV download of the rows shown | Implemented |
| Dataset page: tables, columns, row counts, column meanings, table links, definitions | Implemented |
| Business context file: column meanings, glossary, verified example queries (`backend/app/business_context.json`) | Implemented; shown on the Dataset page, not yet sent to the AI model |
| Rate limit, concurrency cap, daily cap on AI answers | Implemented |
| Evaluation set (62 questions) and runner | Implemented; AI mode not yet measured |
| Richer AI prompt (types, keys, glossary, examples), structured AI replies with retry, clarifying questions | Planned |
| CSV upload, connecting your own database, saved queries | Planned |
| Authentication | Not implemented |

## Demo dataset

A synthetic online software shop, January 2024 to December 2025: 250 customers, 13 products, 2,431 orders and 3,604 order lines. Names are invented and emails use `example.com`. The data is generated from a fixed seed (`backend/app/seed.py`), so every install gets identical rows on any recent Python version.

Definitions used everywhere (demo rules, evaluation, Dataset page) live in `backend/app/business_context.json`, together with what each table and column means and a few verified example queries:

- **Revenue** counts completed orders only; pending and cancelled orders are excluded.
- `shipped_date` is empty (NULL) until an order ships.

The example queries are kept different from the evaluation questions (a test checks this), so they can later be shown to the AI model without inflating its scores.

The data includes cases that test real questions: a product that was never ordered, customers who never ordered, and NULL shipped dates.

## Safety model

Safety does not depend on the AI model behaving well. Each layer works on its own:

1. **Database role.** Queries run as a reader role that can only `SELECT` the exposed tables. PostgreSQL itself enforces read-only mode and statement, lock and idle timeouts for that role. The API refuses to start if its role is a superuser or can write.
2. **Validator.** SQLGlot parses the SQL. Only one `SELECT` is accepted, only on the exposed tables, using only allowlisted functions. A row cap is added without loosening a smaller `LIMIT`.
3. **Execution.** Each query runs in a read-only transaction that is always rolled back.
4. **API.** Per-client rate limit, a cap on concurrent queries, and a daily cap on AI answers. Users see plain error messages with a reference ID; details stay in the server log.
5. **Privacy.** Only table and column names are sent to the AI model, never row data.

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

Measured baseline (2026-10-27, `eval/results/2026-10-27-rules.json`):

| Mode | Correct answers (50) | Wrong answers shown | Declined when it should (12) |
| --- | --- | --- | --- |
| Demo mode (rules) | 12.0% | 0.0% | 100% |
| AI mode | not measured yet | | |

Demo mode answers few questions by design; the important number is that it shows no wrong answers. "Wrong answers shown" counts results that look like answers but are wrong, which users cannot easily spot. Keep the evaluation questions out of any prompt examples, or the scores stop meaning anything.

## Run with Docker

Requirements: Docker Compose.

```bash
cp .env.example .env   # then set POSTGRES_PASSWORD and READER_PASSWORD
docker compose up --build
```

Open <http://localhost:5173>. The API is at <http://localhost:8000>; interactive API docs are at <http://localhost:8000/docs>. A one-off `setup` container creates the tables, demo data and reader role before the API starts. The database port is not published.

**Upgrading from an earlier version:** the demo schema changed. If setup reports "older DataTalk demo schema", reset the local database with `docker compose down -v`, then `docker compose up --build`.

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
pytest
```

## Deploy (Neon + Render + Vercel)

1. **Neon:** create a project and database. From your machine, run setup once with Neon's owner connection string:
   ```bash
   cd backend
   ADMIN_DATABASE_URL="postgresql+psycopg://<neon-owner>:<password>@<neon-host>/<db>?sslmode=require" \
   DATABASE_URL="postgresql+psycopg://datatalk_reader:<new-reader-password>@<neon-host>/<db>?sslmode=require" \
   python -m app.setup_db
   ```
2. **Render:** create a Blueprint from this repo (`render.yaml`). Set `DATABASE_URL` to the **reader** URL above (never the owner URL) and leave `CORS_ORIGINS` for step 4.
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
| `LLM_DAILY_LIMIT` | `200` | AI answers per UTC day before falling back to demo mode (`0` = no cap) |
| `MAX_RESULT_ROWS` | `200` | Maximum rows returned per question |
| `STATEMENT_TIMEOUT_MS` | `5000` | Query time limit |
| `CORS_ORIGINS` | localhost:5173 | Frontend origins allowed to call the API |
| `RATE_LIMIT_PER_MINUTE` | `20` | Questions per client per minute (`0` = no limit) |
| `MAX_CONCURRENT_QUERIES` | `4` | Queries running at once |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Service and database status |
| `GET` | `/api/schema` | Tables with columns, keys, links, meanings and row counts; dataset description and definitions; sample questions; mode (`demo` or `ai`) |
| `POST` | `/api/query` | Generate, validate and run a read-only query |

Example:

```json
POST /api/query
{
  "question": "Show the top 5 products by revenue",
  "max_rows": 100
}
```

## Known limitations

- No authentication; public deployments rely on rate limits and the daily AI cap.
- AI mode accuracy has not been measured yet. The AI model still receives table and column names only; the business context is not yet part of its prompt, and it cannot ask clarifying questions.
- Demo mode only answers a few phrasings by design.
- Rate limits and the daily AI cap are in memory, per API instance.
- Only the bundled demo dataset is supported.
- CSV download contains the rows shown on screen (at most 100 per question), not the full table.
