# DataTalk security model

DataTalk turns natural-language questions into SQL and runs it. The model's output, the user's question and
every uploaded file are treated as **untrusted**. Safety comes from the database permissions and from checks in
trusted application code — never from instructions given to the language model.

This document lists only safeguards that exist in the code, with the tests that exercise them, and the known
limits. It is not a certification; the project has no user accounts and is meant as a demo / self-hosted tool.

## Threats considered

- A question (or a prompt-injected model) producing SQL that changes data, reads other tables, reads server
  files, or runs for a long time.
- A malformed model reply, or the retry being used to turn a refused request into another query.
- A hostile CSV: malicious filenames, headers or cell values; huge or malformed files.
- One visitor reaching another visitor's uploaded dataset.
- A connected database being changed, or read beyond the tables its operator meant to expose.
- Abuse of a public deployment: request floods, storage filling, AI cost.
- Leaks through error messages and logs.

## Database roles (enforced by PostgreSQL)

| Role | Used by | Can do | Cannot do |
| --- | --- | --- | --- |
| Owner | `python -m app.setup_db` only | Create tables, roles, schema | Is never given to the API |
| Reader (`DATABASE_URL`) | Every question | `SELECT` on the demo tables and on each uploaded table | Write anything: `default_transaction_read_only = on`; statement, lock and idle timeouts are set on the role |
| Ingest (`INGEST_DATABASE_URL`, optional) | CSV import and the dataset registry | Create, fill and drop tables in the `uploads` schema | Read the demo tables; create objects in `public` |
| Source role (`SOURCE_<ID>_URL`, optional, one per connected database) | Questions about that database | `SELECT` on the tables its operator granted | Anything DataTalk's checks refuse: see "Databases configured on the server" |

The API refuses to start if the reader is a superuser or not read-only, and refuses to enable uploads if the
ingest role is a superuser or can read the demo tables (`verify_query_role`, `verify_ingest_role`).

Evidence: `tests/test_api_postgres.py::test_query_role_is_read_only_and_not_a_superuser`; the isolation checks in
`tests/test_uploads_postgres.py`.

## SQL checks before execution (`backend/app/safety.py`)

- SQL is parsed with SQLGlot (PostgreSQL dialect); exactly one statement is allowed.
- Only read queries pass; data-changing and locking statements are rejected, including inside CTEs.
- Every table must be in the allowed list **for that request** (the demo tables, the one table of the selected
  upload, or a connected database's tables). Schema-qualified names are rejected.
- CTE names are resolved by scope, the way PostgreSQL resolves them: a non-recursive CTE cannot see itself, a CTE
  inside a subquery is not visible outside it, and unquoted names are folded to lower case first. So
  `WITH t AS (SELECT * FROM t)` still has to pass the allowlist for the real table `t`. A table that no scope
  accounts for makes the query fail closed. (Found by a security review of this branch; before the fix, any CTE
  name anywhere in a query let a table of the same name through.)
- Only allowlisted functions are accepted (aggregates, maths, dates, strings, window functions...). Server
  functions for files, sessions, other databases or running SQL from strings are not on the list.
- A row cap is added without loosening a smaller `LIMIT`.

Evidence: `tests/test_safety.py` (including CTE shadowing cases), plus the CTE cases in
`tests/test_uploads_postgres.py` and `tests/test_sources_postgres.py`.

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
- **Reviewing columns:** a preview parses the file and stores nothing. Reviewed names come back as a header and are
  validated against `^[a-z][a-z0-9_]{0,62}$`, the reserved-word list and each other — rejected, not cleaned up — and
  types must be one of a fixed list. A value that does not fit the chosen type stops the import with the column and
  line named. Lenient readers (currency signs, grouped digits, day/month order) only produce numbers and dates; the
  values still go in through `COPY`.
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

## The language model (`backend/app/generator.py`)

- The model only receives table and column names (for uploads: the private table name and its column names) and
  today's date, never row data. When it is asked again (below), it also gets its previous reply and the problem: the
  validator's message, or PostgreSQL's message and hint for an unknown column or table (SQLSTATE 42703 / 42P01), which
  name columns and tables only. Other database errors are never sent to the model, because their messages can quote
  values (for example a failed cast).
- Its output is validated as above before anything runs; a prompt is not treated as a security boundary.
- **Strict replies.** The reply must be exactly one JSON object in one of two forms: `{"sql", "explanation",
  "assumptions"}` or `{"clarification": {"question", "options"}}`. Unknown or missing keys, wrong types, repeated keys,
  text outside the object (one surrounding markdown code fence is tolerated) and oversized values are refused.
- **One bounded retry.** A malformed reply, an `UnsafeQueryError` marked `retryable`, or an unknown column or table
  gets one more model call per question, at most. A write attempt (`retryable=False`) is never retried: asking again
  could turn a request to change data into an unrelated SELECT that looks like an answer. Timeouts, type errors,
  permission errors and failures to reach the model are not retried either. The retry's SQL goes through the same
  validator, per-request table allowlist, `search_path` and read-only execution as the first try. A reply that is
  still malformed ends with a clear error and nothing runs.
- **Clarifying questions run nothing.** The question and its options are shown as plain text (React escapes them).
  Choosing an option sends it as a new question (each must be 3–200 characters, a valid question), which goes through
  the whole pipeline again. The server keeps no conversation state.
- The explanation and stated assumptions are model-written text shown as plain text. They describe the SQL but are not
  verified against it; "View the SQL that ran" shows what actually ran.
- Questions about uploaded data never fall back to the demo rules, so a demo-table query cannot be produced for an
  upload.
- `LLM_DAILY_LIMIT` caps model calls per day, a retry included (in memory). If the cap is reached before a retry, the
  first failure is reported instead. A provider-side spending limit is still recommended.

Evidence: `tests/test_generator.py` (strict parsing, which failures are retried, at most one retry, writes never
retried, the cap), `tests/test_ai_postgres.py` (clarifications run nothing; a retry that reaches `pg_roles`, a
schema-qualified table, a write or a server-file function is refused before it runs), and the retry cases in
`tests/test_uploads_postgres.py` and `tests/test_sources_postgres.py` (a retry cannot leave its upload or database).

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
  The model's explanation and stated assumptions can be wrong too: they are not checked against the SQL.

## Databases configured on the server (`backend/app/sources.py`)

Visitors cannot type a connection string: on a public site that would let anyone use the server to reach other
machines (SSRF) and would mean holding other people's passwords. Instead, whoever runs DataTalk lists databases in
environment variables (`DATA_SOURCES`, `SOURCE_<ID>_URL`, ...). For each one:

- **Role checks before it is offered**, repeated whenever its table list refreshes (every 5 minutes): the role must
  not be a superuser, must not be able to create roles or databases or replicate, must be read-only by default
  (`default_transaction_read_only = on`), must not belong to `pg_read_server_files`, `pg_write_server_files`,
  `pg_execute_server_program` or `pg_write_all_data`, and must not be able to create objects in, or insert, update,
  delete or truncate any table in, the chosen schema. A database that fails is not offered; the reason is logged.
- **Scope:** questions see only the tables that role can `SELECT` in one schema (plain lowercase names, optional
  allowlist). Grant the role only the tables you mean to expose: the grants are enforced by PostgreSQL, the
  allowlist by DataTalk's validator. The validator allows only those tables and no schema-qualified names, and the query runs with
  `search_path` set to that schema, read-only, with the statement timeout.
- **Access key (optional):** `SOURCE_<ID>_ACCESS_KEY`, sent as `X-Source-Key` and compared in constant time. The
  table list is hidden until the key is given. The browser keeps the key for the current tab only (sessionStorage).
- **Secrets:** connection URLs are read from the environment and never returned or logged; only the source id,
  label and table names are shown.

Evidence: `tests/test_sources_postgres.py` (refused writer, not-read-only, superuser and missing-schema roles; tables
outside the grants, other schemas, demo tables, catalog tables, writes and server-file functions all rejected;
access key required; passwords absent from the log).

Limits: without an access key, anyone who can open the deployment can query a connected database; the access key is
a shared secret, not per-user authentication; network-level protection (TLS with `sslmode=verify-full`, firewalls)
is the operator's responsibility; catalog tables such as `pg_roles` stay readable by name at the database level, so
the table allowlist in the validator is what keeps them out of answers.

## Reporting a problem

Please open a GitHub issue without exploit details, or contact the maintainer privately.
