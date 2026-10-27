from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import settings
from .database import engine
from .evaluation import run_evaluation
from .generator import SAMPLE_QUESTIONS, generate_sql, get_schema_context
from .safety import UnsafeQueryError, validate_read_query
from .schemas import QueryRequest
from .seed import initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="DataTalk API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["*"])


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@app.get("/api/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": engine.dialect.name}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/schema")
def schema():
    tables, _ = get_schema_context()
    return {"tables": tables, "sample_questions": SAMPLE_QUESTIONS}


@app.post("/api/query")
def query(request: QueryRequest):
    tables, context = get_schema_context()
    allowed = [table["name"] for table in tables]
    try:
        candidate, generator, explanation = generate_sql(request.question, context)
        sql = validate_read_query(candidate, allowed, min(request.max_rows, settings.max_result_rows))
        with engine.connect() as connection:
            if engine.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(settings.statement_timeout_ms)}")
            result = connection.execute(text(sql))
            rows = [{key: _json_value(value) for key, value in row.items()} for row in result.mappings().all()]
        return {"question": request.question, "sql": sql, "rows": rows, "row_count": len(rows),
                "truncated": len(rows) >= min(request.max_rows, settings.max_result_rows),
                "generator": generator, "explanation": explanation}
    except UnsafeQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Query could not be completed: {exc}") from exc


@app.post("/api/evaluations/run")
def evaluations():
    return run_evaluation(settings.max_result_rows)

