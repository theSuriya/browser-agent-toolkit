"""Request/response models for the REST API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an absolute http(s) URL")
    return value


class SessionOut(BaseModel):
    id: str


class SessionInfo(BaseModel):
    id: str
    url: str
    created_at: float
    last_used: float


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)
    max_steps: int = Field(default=25, ge=1, le=100)


class SiteCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    required_selectors: list[str] = Field(default_factory=list, max_length=50)
    required_text: list[str] = Field(default_factory=list, max_length=50)
    check_links: bool = True
    max_links: int = Field(default=25, ge=0, le=100)
    screenshot: bool = True

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        return validate_url(value)


class AgentResult(BaseModel):
    ok: bool
    result: str
    steps: int
    session_id: str
    transcript: list[dict[str, Any]] = Field(default_factory=list)


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=list, max_length=10)
    rate_limit: int | None = Field(default=None, ge=1, le=1_000_000)


class KeyCreated(BaseModel):
    id: str
    name: str
    key: str
    scopes: list[str]


class KeyInfo(BaseModel):
    id: str
    name: str
    scopes: list[str]
    rate_limit: int | None
    created_at: float
    revoked: bool
    last_used: float | None


# -- Tenancy / self-serve ---------------------------------------------------
class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    org_name: str = Field(min_length=1, max_length=200)


class SignupResponse(BaseModel):
    org_id: str
    user_id: str
    plan: str
    key: str  # shown once
    key_id: str


class AccountInfo(BaseModel):
    org_id: str
    plan: str
    key_id: str
    key_name: str
    scopes: list[str]
    max_sessions_per_key: int
    max_agent_steps: int


class UsageBucket(BaseModel):
    used: float
    included: int
    remaining: float | None
    overage_cents: float


class UsageSummary(BaseModel):
    plan: str
    period_start: str
    browser_seconds: UsageBucket
    agent_tasks: UsageBucket
    sitechecks: UsageBucket
    agent_steps: float
    screenshots: float


# --- Async jobs (Phase 2) --------------------------------------------------
from datetime import datetime as _dt  # noqa: E402
from typing import Literal as _Literal  # noqa: E402


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: _Literal["agent", "sitecheck"]
    input: dict[str, Any]
    callback_url: str | None = Field(default=None, max_length=2048)

    @field_validator("callback_url")
    @classmethod
    def _validate_callback(cls, value: str | None) -> str | None:
        return value if value is None else validate_url(value)


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    created_at: _dt
    finished_at: _dt | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def from_job(cls, job: Any) -> "JobOut":
        return cls(
            id=job.id,
            kind=job.kind,
            status=job.status,
            created_at=job.created_at,
            finished_at=job.finished_at,
            result=job.result,
            error=job.error,
        )


class JobPage(BaseModel):
    jobs: list[JobOut]
    next_offset: int | None


def agent_max_steps() -> int:
    return get_settings().agent_max_steps
