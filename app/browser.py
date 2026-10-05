"""Playwright lifecycle and per-session state.

One process-wide ``Browser`` is reused; every agent session is an isolated
``BrowserContext`` (its own cookies, storage and page), so sessions never leak
state into each other. Console messages, page errors and failed requests are
captured per session as it runs.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import get_settings
from app.errors import AppError

logger = logging.getLogger(__name__)

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


@dataclass
class Session:
    """An isolated browser context plus the page the agent drives."""

    id: str
    context: BrowserContext
    page: Page
    created_at: float
    last_used: float
    console: list[dict[str, str]] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[dict[str, str]] = field(default_factory=list)
    http_errors: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def reset_recordings(self) -> None:
        self.console.clear()
        self.page_errors.clear()
        self.failed_requests.clear()
        self.http_errors.clear()


class BrowserManager:
    """Owns the shared browser and the register of live sessions."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[str, Session] = {}
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._start_lock:
            if self._browser is not None:
                return
            settings = get_settings()
            self._playwright = await async_playwright().start()
            launcher = getattr(self._playwright, settings.browser_type, None)
            if launcher is None:
                raise AppError(500, "bad_browser", f"Unknown browser type: {settings.browser_type}")
            self._browser = await launcher.launch(headless=settings.headless, args=_LAUNCH_ARGS)
            logger.info("Browser ready: %s (headless=%s)", settings.browser_type, settings.headless)

    async def stop(self) -> None:
        for session_id in list(self._sessions):
            await self.close_session(session_id)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {"id": s.id, "url": s.page.url, "created_at": s.created_at, "last_used": s.last_used}
            for s in self._sessions.values()
        ]

    async def create_session(self) -> Session:
        settings = get_settings()
        await self.start()
        await self.reap_idle()
        if len(self._sessions) >= settings.max_sessions:
            raise AppError(
                429,
                "too_many_sessions",
                f"Session limit ({settings.max_sessions}) reached. Close a session first.",
            )
        assert self._browser is not None
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
        )
        context.set_default_timeout(settings.default_timeout_ms)
        context.set_default_navigation_timeout(settings.default_timeout_ms)
        page = await context.new_page()
        now = time.monotonic()
        session = Session(id=uuid.uuid4().hex[:12], context=context, page=page, created_at=now, last_used=now)
        self._attach_listeners(session)
        self._sessions[session.id] = session
        return session

    def _attach_listeners(self, session: Session) -> None:
        page = session.page

        def on_console(msg) -> None:
            if msg.type in ("error", "warning"):
                session.console.append({"type": msg.type, "text": msg.text})

        def on_request_failed(request) -> None:
            failure = request.failure or ""
            session.failed_requests.append(
                {"url": request.url, "method": request.method, "failure": failure}
            )

        def on_response(response) -> None:
            if response.status >= 400:
                session.http_errors.append({"url": response.url, "status": response.status})

        page.on("console", on_console)
        page.on("pageerror", lambda err: session.page_errors.append(str(err)))
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)

    def get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise AppError(404, "session_not_found", "Unknown session id.")
        session.touch()
        return session

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        try:
            await session.context.close()
        except Exception:  # noqa: BLE001 - the context may already be gone
            logger.debug("Error closing session %s", session_id, exc_info=True)

    async def reap_idle(self) -> None:
        cutoff = time.monotonic() - get_settings().session_idle_seconds
        stale = [sid for sid, s in self._sessions.items() if s.last_used < cutoff]
        for session_id in stale:
            logger.info("Reaping idle session %s", session_id)
            await self.close_session(session_id)


manager = BrowserManager()
