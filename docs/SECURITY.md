# DataTalk security model

DataTalk turns natural-language questions into SQL and runs it. The model's output, the user's question and
every uploaded file are treated as **untrusted**. Safety comes from the database permissions and from checks in
trusted application code — never from instructions given to the language model.

This document lists only safeguards that exist in the code, with the tests that exercise them, and the known
limits. It is not a certification; the project has no user accounts and is meant as a demo / self-hosted tool.

## Threats considered

- A question (or a prompt-injected model) producing SQL that changes data, reads other tables, reads server
  files, or runs for a long time.
- A hostile CSV: malicious filenames, headers or cell values; huge or malformed files.
- One visitor reaching another visitor's uploaded dataset.
- Abuse of a public deployment: request floods, storage filling, AI cost.
- Leaks through error messages and logs.

## Database roles (enforced by PostgreSQL)

| Role | Used by | Can do | Cannot do |
| --- | --- | --- | --- |
| Owner | `python -m app.setup_db` only | Create tables, roles, schema | Is never given to the API |
| Reader (`DATABASE_URL`) | Every question | `SELECT` on the demo tables and on each uploaded table | Write anything: `default_transaction_read_only = on`; statement, lock and idle timeouts are set on the role |
| Ingest (`INGEST_DATABASE_URL`, optional) | CSV import and the dataset registry | Create, fill and drop tables in the `uploads` schema | Read the demo tables; create objects in `public` |

The API refuses to start if the reader is a superuser or not read-only, and refuses to enable uploads if the
ingest role is a superuser or can read the demo tables (`verify_query_role`, `verify_ingest_role`).

Evidence: `tests/test_api_postgres.py::test_query_role_is_read_only_and_not_a_superuser`; the isolation checks in
`tests/test_uploads_postgres.py`.

## SQL checks before execution (`backend/app/safety.py`)

- SQL is parsed with SQLGlot (PostgreSQL dialect); exactly one statement is allowed.
- Only read queries pass; data-changing and locking statements are rejected, including inside CTEs.
- Every table must be in the allowed list **for that request** (the demo tables, or the one table of the
  selected upload). Schema-qualified names are rejected.
- Only allowlisted functions are accepted (aggregates, maths, dates, strings, window functions...). Server
  functions for files, sessions, other databases or running SQL from strings are not on the list.
- A row cap is added without loosening a smaller `LIMIT`.

Evidence: `tests/test_safety.py`.

## Execution (`backend/app/database.py`)

- Each query runs in a `READ ONLY` transaction with `statement_timeout`, and is always rolled back.
- Questions about an upload run with `search_path` set to the `uploads` schema only, so demo tables cannot even
  be found by name from that context.
- A semaphore caps concurrent queries.

## CSV uploads (`backend/app/csv_import.py`, `backend/app/datasets.py`)

- **Limits** (configurable): file size, rows, columns, cell length; uploads per client per minute; total active
  datasets.
- **Parsing:** strict CSV parsing; binary files, unknown encodings, empty files, missing headers, repeated column
  names and ragged rows are rejected with a message naming the problem.
- **Identifiers:** headers never become SQL directly. They are reduced to `[a-z0-9_]` names (reserved words get a
  suffix) and are always quoted with psycopg's identifier quoting. Table names are random (`t_` + 20 hex).
- **Values:** loaded with `COPY` as data, never as SQL text. Types are inferred cautiously; anything doubtful
  (leading zeros, non-ISO dates, thousands separators) stays text.
- **All-or-nothing import:** table creation, data load, grant and registry entry happen in one transaction, so a
  failure leaves no table and no registry row.
- **Ownership:** an upload returns a public dataset id and a secret token. Only a SHA-256 hash of the token is
  stored; reads compare hashes in constant time. Unknown id, wrong token and expired dataset all return the same
  404. The token travels in the `X-Dataset-Token` header, not in URLs.
- **Lifecycle:** datasets expire after `DATASET_TTL_HOURS` (default 24). Expired datasets are unreachable
  immediately; their tables are dropped on the next upload or API start. Users can delete a dataset at any time.
  The original file bytes are not stored.

Evidence: `tests/test_csv_import.py`, `tests/test_uploads_postgres.py` (two different schemas, cross-dataset and
demo-table access refused, writes refused, hostile content stored as plain data, deletion and expiry).

## The language model

- The model only receives table and column names (for uploads: the private table name and its column names),
  never row data.
- Its output is validated as above before anything runs; a prompt is not treated as a security boundary.
- Questions about uploaded data never fall back to the demo rules, so a demo-table query cannot be produced for an
  upload.
- `LLM_DAILY_LIMIT` caps AI answers per day (in memory); a provider-side spending limit is still recommended.

## API

- Rate limits for questions and uploads, request-size checks, a question length limit and validated ids.
- Errors shown to users are plain messages with a reference id; database details, SQL and stack traces go to the
  server log only. Tokens and uploaded values are not logged.
- CORS is limited to the configured frontend origins; credentials are not used.

## Known limitations

- **No user accounts.** Upload ownership is a bearer token kept in the browser's localStorage; anyone holding the
  token can use the dataset until it expires. A script injected into the page could read it (React escapes
  rendered text and the page loads no third-party scripts, but this is not a substitute for real authentication).
- **Isolation between uploads is enforced by the application.** At the database level the reader can `SELECT`
  every uploaded table; the per-request table allowlist, the function allowlist and `search_path` stop one
  dataset's questions from reaching another's table. A per-dataset database role would add another layer.
- Rate limits and the daily AI cap are in memory, per API instance, and reset on restart.
- Expired tables are dropped opportunistically (upload or restart), not by a scheduler.
- Public deployments receive whatever visitors upload. Keep limits small and tell users not to upload personal or
  confidential data (the upload page says so).
- AI-mode accuracy has not been measured; wrong-but-valid SQL is possible. Users should read "View the SQL that ran".

## Connecting an existing PostgreSQL database

Not implemented. Accepting connection strings from browsers would let a public deployment be used to reach
internal networks (SSRF) and would require storing other people's credentials. A safe version needs: an allowlist
of destinations, TLS verification, a user-created read-only role with minimal grants, connection and statement
timeouts, secrets kept server-side and never logged, and real user authentication. Until then, self-hosted users
can point `DATABASE_URL` at their own database with a read-only role (see the README).

## Reporting a problem

Please open a GitHub issue without exploit details, or contact the maintainer privately.
