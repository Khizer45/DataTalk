import logging
import re
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import psycopg
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .csv_import import CsvImportError, display_name, parse_csv
from .database import DatabaseBusyError, execute_read_query, get_engine, verify_query_role
from .datasets import (UPLOADS_SCHEMA, DatasetNotFound, UploadsFull, cleanup_expired, create_dataset, delete_dataset,
                       ensure_registry, get_dataset, schema_text, uploads_enabled, verify_ingest_role)
from .errors import describe_database_error
from .generator import LLMError, generate_sql, llm_enabled
from .limits import RateLimiter
from .rules import SAMPLE_QUESTIONS, UnsupportedQuestionError
from .safety import UnsafeQueryError, validate_read_query
from .schema_info import describe_schema, get_definitions, get_table_row_counts, get_tables
from .schemas import QueryRequest
from .seed import DATASET_INFO

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("datatalk")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_rate_limiter = RateLimiter(settings.rate_limit_per_minute)
_upload_limiter = RateLimiter(settings.upload_rate_limit_per_minute)
_UPLOAD_SUFFIXES = (".csv", ".tsv", ".txt")


@asynccontextmanager
async def lifespan(_: FastAPI):
    verify_query_role(get_engine())
    if uploads_enabled():
        verify_ingest_role()
        ensure_registry()
        logger.info("uploads enabled; removed %d expired datasets", cleanup_expired())
    yield


app = FastAPI(title="DataTalk API", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-Dataset-Token"],
                   expose_headers=["X-Request-ID"])


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    request.state.request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("unhandled error request_id=%s", request_id)
    return JSONResponse(status_code=500, content={"detail": f"Something went wrong (reference {request_id})."},
                        headers={"X-Request-ID": request_id})


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@app.get("/api/health")
def health():
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "postgresql"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/schema")
def schema():
    tables = get_tables()
    counts = get_table_row_counts([table["name"] for table in tables])
    definitions = [f"{item['term']}: {item['definition']}" for item in get_definitions()]
    return {"tables": [{**table, "row_count": counts[table["name"]]} for table in tables],
            "sample_questions": SAMPLE_QUESTIONS, "dataset": {**DATASET_INFO, "definitions": definitions},
            "mode": "ai" if llm_enabled() else "demo",
            "uploads": {"enabled": uploads_enabled(), "max_bytes": settings.upload_max_bytes,
                        "max_rows": settings.upload_max_rows, "max_columns": settings.upload_max_columns,
                        "ttl_hours": settings.dataset_ttl_hours}}


def _require_dataset(dataset_id: str, token: str) -> dict:
    if not uploads_enabled():
        raise HTTPException(status_code=404, detail="CSV uploads are not enabled on this server.")
    try:
        return get_dataset(dataset_id, token)
    except DatasetNotFound as exc:
        raise HTTPException(status_code=404, detail="Dataset not found. It may have expired or been deleted.") from exc


@app.post("/api/datasets", status_code=201)
async def upload_dataset(request: Request, name: str = Query(default="", max_length=200)):
    """Upload a CSV as the raw request body. Returns the dataset and its secret access token (shown once)."""
    if not uploads_enabled():
        raise HTTPException(status_code=404, detail="CSV uploads are not enabled on this server.")
    client = request.client.host if request.client else "unknown"
    if not _upload_limiter.allow(client):
        raise HTTPException(status_code=429, detail="Too many uploads in a short time. Please wait a minute.")
    if name and not name.lower().endswith(_UPLOAD_SUFFIXES):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")
    too_big = f"The file is larger than {settings.upload_max_bytes / 1024 / 1024:g} MB."
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > settings.upload_max_bytes:
        raise HTTPException(status_code=413, detail=too_big)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings.upload_max_bytes:
            raise HTTPException(status_code=413, detail=too_big)
    try:
        parsed = await run_in_threadpool(parse_csv, bytes(body))
        dataset = await run_in_threadpool(create_dataset, parsed, display_name(name))
    except CsvImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadsFull as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except psycopg.Error as exc:
        request_id = request.state.request_id
        logger.warning("import failed request_id=%s error=%s", request_id, type(exc).__name__)
        raise HTTPException(status_code=422, detail=f"The data could not be imported (reference {request_id}).") from exc
    try:
        await run_in_threadpool(cleanup_expired)
    except psycopg.Error:
        logger.warning("cleanup of expired datasets failed", exc_info=True)
    logger.info("dataset created id=%s rows=%d columns=%d", dataset["id"], dataset["row_count"], len(dataset["columns"]))
    return {**dataset, "warnings": parsed.warnings}


@app.get("/api/datasets/{dataset_id}")
def dataset_info(dataset_id: str, x_dataset_token: str = Header(default="")):
    dataset = _require_dataset(dataset_id, x_dataset_token)
    return {key: value for key, value in dataset.items() if key != "table_name"}


@app.delete("/api/datasets/{dataset_id}", status_code=204)
def remove_dataset(dataset_id: str, x_dataset_token: str = Header(default="")):
    _require_dataset(dataset_id, x_dataset_token)
    try:
        delete_dataset(dataset_id, x_dataset_token)
    except DatasetNotFound as exc:
        raise HTTPException(status_code=404, detail="Dataset not found. It may have expired or been deleted.") from exc
    return Response(status_code=204)


@app.post("/api/query")
def query(payload: QueryRequest, request: Request, x_dataset_token: str = Header(default="")):
    request_id = request.state.request_id
    client = request.client.host if request.client else "unknown"
    if not _rate_limiter.allow(client):
        raise HTTPException(status_code=429, detail="Too many questions in a short time. Please wait a minute.")

    limit = min(payload.max_rows, settings.max_result_rows)
    # An uploaded dataset is only reachable with its token, and its questions may only touch its one table.
    dataset = _require_dataset(payload.dataset_id, x_dataset_token) if payload.dataset_id else None
    try:
        if dataset:
            candidate, generator, explanation = generate_sql(payload.question, schema_text(dataset), allow_rules=False)
            allowed = [dataset["table_name"]]
        else:
            candidate, generator, explanation = generate_sql(payload.question, describe_schema("basic"))
            allowed = [table["name"] for table in get_tables()]
        # Fetch one extra row so truncation is reported accurately.
        sql = validate_read_query(candidate, allowed, limit + 1)
    except UnsupportedQuestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsafeQueryError as exc:
        logger.warning("rejected query request_id=%s reason=%s", request_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        logger.warning("llm failure request_id=%s cause=%r", request_id, exc.__cause__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        raw_rows = execute_read_query(sql, search_path=UPLOADS_SCHEMA if dataset else None)
    except DatabaseBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.warning("query failed request_id=%s sql=%r error=%s", request_id, sql, exc)
        status, message = describe_database_error(exc)
        raise HTTPException(status_code=status, detail=f"{message} (reference {request_id})") from exc

    rows = [{key: _json_value(value) for key, value in row.items()} for row in raw_rows[:limit]]
    logger.info("query ok request_id=%s generator=%s source=%s rows=%d", request_id, generator,
                f"dataset:{dataset['id']}" if dataset else "demo", len(rows))
    return {"question": payload.question, "sql": sql, "rows": rows, "row_count": len(rows),
            "truncated": len(raw_rows) > limit, "generator": generator, "explanation": explanation,
            "source": "upload" if dataset else "demo", "dataset_name": dataset["name"] if dataset else None}
