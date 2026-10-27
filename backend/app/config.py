import os


def _csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class Settings:
    # Read-only role used for every user query. The app refuses to start if this role can write.
    database_url: str = os.getenv("DATABASE_URL", "")
    # Owner role, used only by `python -m app.setup_db` to create tables, seed data and the reader role.
    admin_database_url: str = os.getenv("ADMIN_DATABASE_URL", "")
    exposed_tables: list[str] = _csv("EXPOSED_TABLES", "customers,products,orders,order_items")
    # Descriptions, glossary and verified examples for the model (default: app/business_context.json).
    business_context_path: str = os.getenv("BUSINESS_CONTEXT_PATH", "")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    # AI-answered questions per UTC day before falling back to demo mode (0 = no cap).
    llm_daily_limit: int = int(os.getenv("LLM_DAILY_LIMIT", "200"))
    # "full" sends types, keys, descriptions, glossary and examples; "basic" sends names only (for comparisons).
    llm_context: str = os.getenv("LLM_CONTEXT", "full")
    # Extra attempts when the model's reply is unusable or its SQL fails in a fixable way.
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "1"))
    # Ask for a JSON reply via response_format; turn off for providers that reject the parameter.
    llm_json_mode: bool = os.getenv("LLM_JSON_MODE", "true").lower() == "true"
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

    max_result_rows: int = int(os.getenv("MAX_RESULT_ROWS", "200"))
    statement_timeout_ms: int = int(os.getenv("STATEMENT_TIMEOUT_MS", "5000"))

    cors_origins: list[str] = _csv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
    max_concurrent_queries: int = int(os.getenv("MAX_CONCURRENT_QUERIES", "4"))


settings = Settings()
