import os


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./datatalk.db")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    max_result_rows: int = int(os.getenv("MAX_RESULT_ROWS", "200"))
    statement_timeout_ms: int = int(os.getenv("STATEMENT_TIMEOUT_MS", "5000"))


settings = Settings()

