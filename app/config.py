"""Application settings, loaded once from the environment / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    app_name: str = "Browser Agent Toolkit"
    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = ["*"]

    # Auth (API keys)
    require_auth: bool = True
    api_keys: str = ""  # comma-separated bootstrap keys, each granted the admin scope
    db_path: str = "data/keys.db"
    # Full DSN. Empty means "derive a SQLite DSN from DB_PATH" (dev and tests).
    database_url: str = ""
    rate_limit_per_min: int = 120
    max_sessions_per_key: int = 4
    mcp_http_path: str = "/mcp"
    default_plan: str = "free"

    # Shared services (Redis): global rate limiting, session registry, job queue.
    # Empty URL disables Redis and falls back to per-process behaviour.
    redis_url: str = "redis://localhost:6379/0"

    # Billing (Stripe). Optional until billing is switched on.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Async jobs / worker
    run_inline_worker: bool = True       # run a worker inside the API process (single node)
    jobs_max_concurrent_per_org: int = 2  # per-tenant fairness cap in the worker
    job_max_attempts: int = 2             # a job whose worker crashes is retried once
    job_lease_seconds: int = 300          # re-queue jobs stuck "running" past this
    webhook_timeout_seconds: float = 10.0
    webhook_signing_secret: str = ""      # HMAC-SHA256 key for X-BAT-Signature on webhooks
    worker_poll_seconds: float = 1.0

    # Artifacts (S3, or local disk with signed URLs)
    artifact_base_url: str = "http://localhost:8080"
    artifact_signing_secret: str = ""     # required for local signed URLs in production
    artifact_ttl_seconds: int = 3600
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""

    # Session registry (Redis)
    session_registry_ttl_seconds: int = 1800

    # Browser
    browser_type: str = "chromium"
    headless: bool = True
    default_timeout_ms: int = 30_000
    max_sessions: int = 8
    session_idle_seconds: int = 900
    artifacts_dir: str = "artifacts"

    # LLM (OpenAI-compatible chat completions)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    agent_max_steps: int = 25

    # Limits
    max_text_chars: int = 8000
    max_html_chars: int = 20_000

    @property
    def bootstrap_keys(self) -> list[str]:
        """Bootstrap admin keys, parsed from the comma-separated ``API_KEYS`` value."""
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]

    @property
    def effective_database_url(self) -> str:
        """The DSN to use: an explicit ``DATABASE_URL`` or a SQLite file from ``DB_PATH``."""
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
