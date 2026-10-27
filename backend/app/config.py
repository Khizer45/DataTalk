import os


def _csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class Settings:
    # Read-only role used for every user query. The app refuses to start if this role can write.
    database_url: str = os.getenv("DATABASE_URL", "")
    # Owner role, used only by `python -m app.setup_db` to create tables, seed data and the reader role.
    admin_database_url: str = os.getenv("ADMIN_DATABASE_URL", "")
    exposed_tables: list[str] = _csv("EXPOSED_TABLES", "customers,products,orders,order_items")
    # Table and column meanings, glossary and verified examples (default: app/business_context.json).
    business_context_path: str = os.getenv("BUSINESS_CONTEXT_PATH", "")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    # AI-answered questions per UTC day before falling back to demo mode (0 = no cap).
    llm_daily_limit: int = int(os.getenv("LLM_DAILY_LIMIT", "200"))

    max_result_rows: int = int(os.getenv("MAX_RESULT_ROWS", "200"))
    statement_timeout_ms: int = int(os.getenv("STATEMENT_TIMEOUT_MS", "5000"))

    # CSV uploads are off unless INGEST_DATABASE_URL is set: a limited role that can only create tables in the
    # uploads schema (never the owner, never the query reader).
    ingest_database_url: str = os.getenv("INGEST_DATABASE_URL", "")
    upload_max_bytes: int = int(os.getenv("UPLOAD_MAX_BYTES", str(5 * 1024 * 1024)))
    upload_max_rows: int = int(os.getenv("UPLOAD_MAX_ROWS", "50000"))
    upload_max_columns: int = int(os.getenv("UPLOAD_MAX_COLUMNS", "50"))
    upload_max_cell_chars: int = int(os.getenv("UPLOAD_MAX_CELL_CHARS", "1000"))
    dataset_ttl_hours: int = int(os.getenv("DATASET_TTL_HOURS", "24"))
    max_active_datasets: int = int(os.getenv("MAX_ACTIVE_DATASETS", "100"))
    upload_rate_limit_per_minute: int = int(os.getenv("UPLOAD_RATE_LIMIT_PER_MINUTE", "5"))

    cors_origins: list[str] = _csv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
    max_concurrent_queries: int = int(os.getenv("MAX_CONCURRENT_QUERIES", "4"))


settings = Settings()
