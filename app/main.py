"""FastAPI application: sessions, agent runs, site checks, health, tool discovery."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.browser import manager
from app.config import get_settings
from app.errors import register_error_handlers
from app.routers import agents, browser, sitecheck
from app.tools import TOOL_SPECS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def _reaper() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await manager.reap_idle()
        except Exception:  # noqa: BLE001 - background task must not die
            logger.exception("Session reaper failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The browser launches lazily on the first session, so the API boots without it.
    reaper = asyncio.create_task(_reaper())
    yield
    reaper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reaper
    await manager.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(browser.router)
    app.include_router(agents.router)
    app.include_router(sitecheck.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "sessions": manager.session_count}

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

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
