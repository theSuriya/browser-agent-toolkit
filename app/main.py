"""FastAPI application: sessions, agent runs, site checks, admin keys, health.

The MCP server is mounted at ``/mcp`` so a single auth middleware protects both the
REST API and the MCP HTTP endpoint.
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import KeyStore
from app.browser import manager
from app.config import get_settings
from app.errors import register_error_handlers
from app.mcp_server import build_mcp
from app.middleware import AccessLogMiddleware, AuthMiddleware, SecurityHeadersMiddleware
from app.ratelimit import SlidingWindowLimiter
from app.routers import admin, agents, browser, sitecheck
from app.tools import TOOL_SPECS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _noop_lifespan(_app: FastAPI):
    """Neutral lifespan for the mounted MCP app: we start its session manager ourselves."""
    yield


async def _reaper() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await manager.reap_idle()
        except Exception:  # noqa: BLE001 - a background task must not die silently
            logger.exception("Session reaper failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    reaper = asyncio.create_task(_reaper())
    try:
        # The MCP streamable-http transport needs its session manager running for the
        # lifetime of the app. It may be started only once per instance, so a second
        # lifespan entry on the same app (as happens in tests) is a no-op.
        if not getattr(app.state, "mcp_started", False):
            app.state.mcp_started = True
            async with app.state.mcp.session_manager.run():
                yield
        else:
            yield
    finally:
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper
        await manager.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

    store = KeyStore(settings.db_path)
    store.ensure_bootstrap(settings.bootstrap_keys)
    app.state.key_store = store
    app.state.limiter = SlidingWindowLimiter()

    # Outermost middleware is added last: CORS -> access log -> security -> auth.
    app.add_middleware(
        AuthMiddleware,
        settings=settings,
        store=store,
        limiter=app.state.limiter,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(admin.router)
    app.include_router(browser.router)
    app.include_router(agents.router)
    app.include_router(sitecheck.router)

    # Build a per-app MCP server: its streamable-http session manager can only be
    # started once, so each app (and each test) needs its own instance.
    mcp_server = build_mcp()
    mcp_server.settings.host = settings.host
    mcp_server.settings.port = settings.port
    # Inner path "/" so the endpoint lands exactly at settings.mcp_http_path ("/mcp").
    mcp_server.settings.streamable_http_path = "/"
    app.state.mcp = mcp_server

    mcp_http_app = mcp_server.streamable_http_app()
    mcp_http_app.router.lifespan_context = _noop_lifespan  # started in our lifespan instead
    app.mount(settings.mcp_http_path, mcp_http_app)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "sessions": manager.session_count, "auth": settings.require_auth}

    @app.get("/tools", tags=["meta"])
    async def tools() -> dict:
        return {
            "tools": [
                {"name": spec["function"]["name"], "description": spec["function"]["description"]}
                for spec in TOOL_SPECS
            ]
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run("app.main:app", host=_settings.host, port=_settings.port)
