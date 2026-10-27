# DataTalk — Natural Language Database Analyst

DataTalk turns plain-English questions into grounded, read-only SQL and presents the results in a small analytics dashboard. It ships with sample sales data so the full flow works without an LLM key; configure an OpenAI-compatible endpoint to use an LLM for SQL generation.

## Features

- FastAPI API with PostgreSQL and SQLAlchemy (SQLite is available for a quick local run).
- Schema introspection supplies only table and column metadata to the SQL generator.
- SQLGlot parses and validates every generated statement; only a single `SELECT` query over known tables is accepted.
- PostgreSQL runs each query in a read-only transaction with a statement timeout, and result rows are capped.
- Dashboard with prompt suggestions, SQL preview, result table, and a compact chart.
- Curated evaluation run reports SQL/result correctness, execution success, and response accuracy against trusted reference queries.

## Run with Docker

Requirements: Docker Compose.

```bash
docker compose up --build
```

Open <http://localhost:5173>. The API is at <http://localhost:8000>; interactive API docs are at <http://localhost:8000/docs>.

The default Compose configuration connects to PostgreSQL and seeds a small demo dataset on startup. To enable LLM generation, copy `.env.example` to `.env` and set `LLM_API_KEY` and `LLM_MODEL`. `LLM_BASE_URL` defaults to the OpenAI-compatible `/v1` API base.

## Run locally without Docker

Use Python 3.11+ and Node.js 20+.

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Local backend defaults to `sqlite:///./datatalk.db`. Set `DATABASE_URL` to a PostgreSQL URL to use PostgreSQL instead. Vite proxies `/api` requests to `http://localhost:8000`.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Service and database status |
| `GET` | `/api/schema` | Available tables, columns, and sample questions |
| `POST` | `/api/query` | Generate, validate, and run a read-only query |
| `POST` | `/api/evaluations/run` | Run the built-in semantic SQL benchmark |

Example:

```json
POST /api/query
{
  "question": "Show the top 5 products by revenue",
  "max_rows": 100
}
```

The app is a development/demo foundation. For production, use a dedicated database account with `SELECT` grants only, authenticate the API, and review schema/table access before connecting business data.

