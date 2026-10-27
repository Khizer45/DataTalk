import logging
import re
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .database import DatabaseBusyError, execute_read_query, get_engine, verify_query_role
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    verify_query_role(get_engine())
    yield


app = FastAPI(title="DataTalk API", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"], expose_headers=["X-Request-ID"])


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
            "mode": "ai" if llm_enabled() else "demo"}


@app.post("/api/query")
def query(payload: QueryRequest, request: Request):
    request_id = request.state.request_id
    client = request.client.host if request.client else "unknown"
    if not _rate_limiter.allow(client):
        raise HTTPException(status_code=429, detail="Too many questions in a short time. Please wait a minute.")

    limit = min(payload.max_rows, settings.max_result_rows)
    try:
        candidate, generator, explanation = generate_sql(payload.question, describe_schema("basic"))
        # Fetch one extra row so truncation is reported accurately.
        sql = validate_read_query(candidate, [table["name"] for table in get_tables()], limit + 1)
    except UnsupportedQuestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsafeQueryError as exc:
        logger.warning("rejected query request_id=%s reason=%s", request_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        logger.warning("llm failure request_id=%s cause=%r", request_id, exc.__cause__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        raw_rows = execute_read_query(sql)
    except DatabaseBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.warning("query failed request_id=%s sql=%r error=%s", request_id, sql, exc)
        status, message = describe_database_error(exc)
        raise HTTPException(status_code=status, detail=f"{message} (reference {request_id})") from exc

    rows = [{key: _json_value(value) for key, value in row.items()} for row in raw_rows[:limit]]
    logger.info("query ok request_id=%s generator=%s rows=%d", request_id, generator, len(rows))
    return {"question": payload.question, "sql": sql, "rows": rows, "row_count": len(rows),
            "truncated": len(raw_rows) > limit, "generator": generator, "explanation": explanation}
