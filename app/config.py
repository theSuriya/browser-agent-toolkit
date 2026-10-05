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
    rate_limit_per_min: int = 120
    max_sessions_per_key: int = 4
    mcp_http_path: str = "/mcp"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
