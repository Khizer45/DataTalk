# DataTalk — Natural Language Database Analyst

DataTalk turns plain-English questions into grounded, read-only SQL and presents the results in a small analytics dashboard. It ships with sample sales data so the full flow works without an LLM key; configure an OpenAI-compatible endpoint to use an LLM for SQL generation.

## Features

- FastAPI API on PostgreSQL (PostgreSQL only).
- Two database roles: an owner used once by `python -m app.setup_db`, and a read-only reader used for every user query. PostgreSQL enforces read-only mode and timeouts on the reader role itself, and the API refuses to start if its role is a superuser or can write.
- SQLGlot parses every generated statement. Only a single `SELECT` over the exposed tables, using an allowlist of common functions, is accepted. Row caps never loosen a smaller `LIMIT`.
- Each query runs in a read-only, time-limited transaction. Users see plain error messages with a reference ID; details go to the server log.
- Per-client rate limit and a cap on concurrent queries.
- Dashboard with prompt suggestions, SQL preview, result table, and a compact chart.
- Without an LLM key, a small rule-based demo mode answers the suggested questions and says when it can't answer something.

## Run with Docker

Requirements: Docker Compose.

```bash
cp .env.example .env   # then set POSTGRES_PASSWORD and READER_PASSWORD
docker compose up --build
```

Open <http://localhost:5173>. The API is at <http://localhost:8000>; interactive API docs are at <http://localhost:8000/docs>. A one-off `setup` container creates the tables, demo data and reader role before the API starts. The database port is not published.

To enable LLM generation, set `LLM_API_KEY` and `LLM_MODEL` in `.env`. `LLM_BASE_URL` defaults to the OpenAI-compatible `/v1` API base.

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

Free tiers sleep when idle, so the first request after a pause can be slow. If you add an LLM key, set a spending limit with the provider.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Service and database status |
| `GET` | `/api/schema` | Available tables, columns, and sample questions |
| `POST` | `/api/query` | Generate, validate, and run a read-only query |
| `POST` | `/api/evaluations/run` | Placeholder benchmark; disabled unless `ENABLE_EVAL_ENDPOINT=true` (or run `python -m app.evaluation`) |

Example:

```json
POST /api/query
{
  "question": "Show the top 5 products by revenue",
  "max_rows": 100
}
```

## Known limitations

- No authentication yet; deploy publicly only with the rate limits configured.
- The built-in evaluation is a placeholder: in demo mode it reuses its own reference queries, so its score says nothing about accuracy. A real evaluation dataset is planned.
- LLM output is not yet structured, so the app cannot ask clarifying questions.
- Rate limits are in memory, per API instance.
