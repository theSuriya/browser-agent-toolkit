"""Minimal async client for any OpenAI-compatible chat completions endpoint.

Works unchanged with OpenAI, OpenRouter, Groq, Together, Ollama (/v1), vLLM and
LM Studio — anything that speaks the ``/chat/completions`` tool-calling format.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.errors import AppError

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0")


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.temperature = temperature if temperature is not None else settings.llm_temperature

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif not any(host in self.base_url for host in _LOCAL_HOSTS):
            raise AppError(500, "llm_not_configured", "Set LLM_API_KEY to run the agent.")
        return headers

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._auth_headers(),
            )
        if response.status_code >= 400:
            raise AppError(
                502,
                "llm_error",
                f"LLM returned {response.status_code}: {response.text[:300]}",
            )
        try:
            return response.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise AppError(502, "llm_bad_response", "LLM response had no message.") from exc
