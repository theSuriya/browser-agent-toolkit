"""Browser worker: consumes jobs and runs them with per-tenant fairness and retry.

Run one worker per process with ``python -m app.worker``. A single node can also run
an inline worker inside the API process (``RUN_INLINE_WORKER=true``), the default so a
fresh clone works with no extra process and no Redis.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time

from app import jobs as job_store
from app.browser import manager
from app.config import Settings, get_settings
from app.metering import meter
from app.registry import SessionRegistry

logger = logging.getLogger("app.worker")


class Worker:
    def __init__(
        self,
        *,
        sessionmaker,
        queue,
        settings: Settings | None = None,
        registry: SessionRegistry | None = None,
        executor=None,
        worker_id: str | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._queue = queue
        self._settings = settings or get_settings()
        self._registry = registry
        self._executor = executor or self._default_executor
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self._org_active: dict[str, int] = {}

    # -- per-tenant fairness -------------------------------------------------
    def _at_capacity(self, org_id: str) -> bool:
        return self._org_active.get(org_id, 0) >= self._settings.jobs_max_concurrent_per_org

    def _admit(self, org_id: str) -> None:
        self._org_active[org_id] = self._org_active.get(org_id, 0) + 1

    def _release(self, org_id: str) -> None:
        remaining = self._org_active.get(org_id, 1) - 1
        if remaining <= 0:
            self._org_active.pop(org_id, None)
        else:
            self._org_active[org_id] = remaining

    # -- run loop ------------------------------------------------------------
    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info("worker %s started", self.worker_id)
        try:
            while not stop_event.is_set():
                envelope = await self._dequeue()
                if envelope is None:
                    continue
                org_id = envelope["org_id"]
                if self._at_capacity(org_id):
                    # Defer so one busy tenant cannot starve the others.
                    await self._queue.enqueue(envelope)
                    await asyncio.sleep(self._settings.worker_poll_seconds)
                    continue
                self._admit(org_id)
                try:
                    await self._process(envelope)
                finally:
                    self._release(org_id)
        finally:
            logger.info("worker %s stopped", self.worker_id)

    async def _dequeue(self) -> dict | None:
        try:
            return await self._queue.dequeue(self._settings.worker_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the worker alive on transient Redis errors
            logger.warning("dequeue failed: %s", exc)
            await asyncio.sleep(self._settings.worker_poll_seconds)
            return None

    async def process_once(self, timeout: float = 1.0) -> bool:
        """Process at most one queued job. Returns True when a job ran."""
        envelope = await self._queue.dequeue(timeout)
        if envelope is None:
            return False
        org_id = envelope["org_id"]
        self._admit(org_id)
        try:
            await self._process(envelope)
        finally:
            self._release(org_id)
        return True

    # -- job lifecycle -------------------------------------------------------
    async def _process(self, envelope: dict) -> None:
        job_id = envelope["job_id"]
        org_id = envelope["org_id"]
        callback_url = envelope.get("callback_url")
        async with self._sessionmaker() as session:
            await job_store.mark_running(session, job_id, self.worker_id)

        attempts = 0
        last_error: str | None = None
        while attempts < self._settings.job_max_attempts:
            attempts += 1
            try:
                outcome = await self._executor(envelope, self._sessionmaker)
            except Exception as exc:  # noqa: BLE001 - retry once, then fail the job honestly
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "job %s attempt %d/%d failed: %s",
                    job_id,
                    attempts,
                    self._settings.job_max_attempts,
                    last_error,
                )
                continue
            async with self._sessionmaker() as session:
                await job_store.mark_finished(
                    session, job_id, job_store.STATUS_SUCCEEDED, result=outcome.get("result"), attempts=attempts
                )
                for metric, quantity in (outcome.get("usage") or {}).items():
                    await meter(
                        session,
                        org_id,
                        metric,
                        float(quantity),
                        key_id=envelope.get("key_id"),
                        job_id=job_id,
                    )
            await self._webhook(callback_url, job_id, job_store.STATUS_SUCCEEDED, result=outcome.get("result"), error=None)
            return

        async with self._sessionmaker() as session:
            await job_store.mark_finished(
                session, job_id, job_store.STATUS_FAILED, error=last_error, attempts=attempts
            )
        logger.error("job %s failed after %d attempt(s): %s", job_id, attempts, last_error)
        await self._webhook(callback_url, job_id, job_store.STATUS_FAILED, result=None, error=last_error)

    async def _webhook(self, url, job_id, status, *, result, error) -> None:
        if not url:
            return
        await job_store.deliver_webhook(
            url,
            {"job_id": job_id, "status": status, "result": result, "error": error},
            secret=self._settings.webhook_signing_secret,
            timeout=self._settings.webhook_timeout_seconds,
        )

    # -- default executor ----------------------------------------------------
    async def _default_executor(self, envelope: dict, sessionmaker) -> dict:
        kind = envelope["kind"]
        payload = envelope.get("payload", {})
        org_id = envelope["org_id"]
        session = await manager.create_session(
            owner=org_id, max_per_owner=self._settings.max_sessions_per_key
        )
        if self._registry is not None:
            await self._registry.register(session.id, org_id, worker_id=self.worker_id)
        started = time.monotonic()
        try:
            if kind == "agent":
                from app.agent import run_agent

                result = await run_agent(
                    session,
                    payload.get("task", ""),
                    max_steps=payload.get("max_steps") or self._settings.agent_max_steps,
                )
                steps = float(result.get("steps", 0) or 0)
                usage = {
                    "browser_seconds": time.monotonic() - started,
                    "agent_tasks": 1,
                    "agent_steps": steps,
                }
            elif kind == "sitecheck":
                from app.sitecheck import run_site_check

                result = await run_site_check(
                    session,
                    payload.get("url"),
                    required_selectors=payload.get("required_selectors"),
                    required_text=payload.get("required_text"),
                    check_links=payload.get("check_links", True),
                    max_links=payload.get("max_links", 25),
                    screenshot=payload.get("screenshot", True),
                )
                usage = {"browser_seconds": time.monotonic() - started, "sitecheck": 1}
            else:
                raise ValueError(f"unsupported job kind: {kind}")
        finally:
            await manager.close_session(session.id)
            if self._registry is not None:
                await self._registry.deregister(session.id)
        return {"result": result, "usage": usage}


async def main() -> None:
    """Standalone worker entrypoint: ``python -m app.worker``."""
    import signal

    from app.cache import make_redis
    from app.db import init_models, make_engine, make_sessionmaker
    from app.queue import build_queue

    settings = get_settings()
    engine = make_engine(settings.effective_database_url)
    sessionmaker = make_sessionmaker(engine)
    await init_models(engine)

    redis = make_redis(settings.redis_url)
    queue = build_queue(redis)
    registry = SessionRegistry(redis) if redis is not None else None

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    worker = Worker(sessionmaker=sessionmaker, queue=queue, settings=settings, registry=registry)
    logger.info("worker %s online (redis=%s)", worker.worker_id, bool(redis))
    try:
        await worker.run(stop_event)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
