"""FastAPI application: sessions, agents, site checks, jobs, accounts, admin, health.

The API only enqueues work; a browser worker (``app/worker.py``) executes it. On a
single node the API can also run that worker inline (``RUN_INLINE_WORKER``), so a
fresh clone needs neither Redis nor a second process to work end to end.
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import repository
from app.auth import KeyStore
from app.browser import manager
from app.cache import make_redis
from app.config import get_settings
from app.db import init_models, make_engine, make_sessionmaker
from app.errors import register_error_handlers
from app.mcp_server import build_mcp
from app.middleware import AccessLogMiddleware, AuthMiddleware, SecurityHeadersMiddleware
from app.queue import build_queue
from app.ratelimit import build_limiter
from app.registry import SessionRegistry
from app.routers import accounts, admin, agents, artifacts, browser, jobs, sitecheck, usage
from app.storage import build_store
from app.tools import TOOL_SPECS

logger = logging.getLogger(__name__)


async def _session_reaper() -> None:
    reap = getattr(manager, "reap_idle", None)
    if reap is None:
        return
    while True:
        await asyncio.sleep(60)
        try:
            await reap()
        except Exception:  # noqa: BLE001 - the reaper must never kill the process
            logger.exception("Session reaper failed")


async def _job_reaper(app: FastAPI) -> None:
    """Requeue jobs whose worker stopped mid-run (lease expired)."""
    from app import jobs as job_store

    settings = app.state.settings
    interval = max(30, settings.job_lease_seconds // 2)
    while True:
        await asyncio.sleep(interval)
        try:
            async with app.state.sessionmaker() as session:
                stale = await job_store.reap_stale(session, settings.job_lease_seconds)
            for envelope in stale:
                await app.state.queue.enqueue(envelope)
            if stale:
                logger.warning("requeued %d stale job(s)", len(stale))
        except Exception:  # noqa: BLE001
            logger.exception("Job reaper failed")


async def _run_inline_worker(app: FastAPI, stop_event: asyncio.Event) -> None:
    from app.worker import Worker

    worker = Worker(
        sessionmaker=app.state.sessionmaker,
        queue=app.state.queue,
        settings=app.state.settings,
        registry=app.state.registry,
        executor=app.state.worker_executor,
    )
    await worker.run(stop_event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_reaper = asyncio.create_task(_session_reaper())
    job_reaper = asyncio.create_task(_job_reaper(app))

    factory = app.state.sessionmaker
    await init_models(app.state.engine)
    async with factory() as session:
        await repository.seed_plans(session)
    await app.state.key_store.ensure_bootstrap(app.state.settings.bootstrap_keys)

    stop_event = asyncio.Event()
    app.state.stop_event = stop_event
    worker_task: asyncio.Task | None = None
    if app.state.settings.run_inline_worker:
        worker_task = asyncio.create_task(_run_inline_worker(app, stop_event))

    try:
        if not getattr(app.state, "mcp_started", False):
            app.state.mcp_started = True
            async with app.state.mcp.session_manager.run():
                yield
        else:
            yield
    finally:
        stop_event.set()
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        for task in (session_reaper, job_reaper):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await manager.stop()
        await app.state.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.1.0", lifespan=lifespan)

    engine = make_engine(settings.effective_database_url)
    sessionmaker = make_sessionmaker(engine)
    store = KeyStore(sessionmaker)

    redis = make_redis(settings.redis_url)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.key_store = store
    app.state.redis = redis
    app.state.limiter = build_limiter(redis)
    app.state.queue = build_queue(redis)
    app.state.registry = SessionRegistry(redis) if redis is not None else None
    app.state.storage = build_store(settings)
    # Tests may inject a fake executor; production uses the browser default.
    app.state.worker_executor = None

    mcp = build_mcp()
    app.state.mcp = mcp
    app.mount(settings.mcp_http_path, mcp.streamable_http_app())

    app.add_middleware(AuthMiddleware, settings=settings, store=store, limiter=app.state.limiter)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(accounts.router)
    app.include_router(admin.router)
    app.include_router(browser.router)
    app.include_router(agents.router)
    app.include_router(sitecheck.router)
    app.include_router(jobs.router)
    app.include_router(artifacts.router)
    app.include_router(usage.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok", "version": app.version}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict:
        checks = {"database": "unknown", "redis": "disabled"}
        try:
            async with sessionmaker() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:  # noqa: BLE001 - readiness reports, it does not raise
            checks["database"] = "error"
        if app.state.redis is not None:
            try:
                await app.state.redis.ping()
                checks["redis"] = "ok"
            except Exception:  # noqa: BLE001
                checks["redis"] = "error"
        return {
            "status": "ready" if checks["database"] == "ok" else "degraded",
            "checks": checks,
        }

    @app.get("/tools", tags=["tools"])
    async def tools() -> dict:
        return {"tools": [spec["function"] for spec in TOOL_SPECS]}

    return app


app = create_app()
