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


def agent_max_steps() -> int:
    return get_settings().agent_max_steps
