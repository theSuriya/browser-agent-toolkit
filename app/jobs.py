"""Async job persistence, queue envelopes and completion webhooks.

The HTTP layer only enqueues; a browser worker (``app/worker.py``) does the work.
This module owns the database side (create / read / transition) and webhook
delivery, so the API and the worker share one implementation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_job(
    session: AsyncSession,
    *,
    org_id: str,
    key_id: str | None,
    kind: str,
    payload: dict,
    callback_url: str | None = None,
) -> Job:
    job = Job(
        org_id=org_id,
        key_id=key_id,
        kind=kind,
        status=STATUS_QUEUED,
        input={"payload": payload, "callback_url": callback_url},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, org_id: str, job_id: str) -> Job | None:
    return await session.scalar(select(Job).where(Job.id == job_id, Job.org_id == org_id))


async def list_jobs(
    session: AsyncSession, org_id: str, limit: int, offset: int
) -> tuple[list[Job], int | None]:
    rows = (
        await session.scalars(
            select(Job)
            .where(Job.org_id == org_id)
            .order_by(Job.created_at.desc(), Job.id)
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return list(rows[:limit]), (offset + limit if len(rows) > limit else None)


def envelope(job: Job) -> dict:
    """The queue message a worker consumes."""
    data = job.input or {}
    return {
        "job_id": job.id,
        "org_id": job.org_id,
        "key_id": job.key_id,
        "kind": job.kind,
        "payload": data.get("payload", {}),
        "callback_url": data.get("callback_url"),
    }


async def mark_running(session: AsyncSession, job_id: str, worker_id: str) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.status = STATUS_RUNNING
    job.worker_id = worker_id
    job.started_at = _now()
    await session.commit()


async def mark_finished(
    session: AsyncSession,
    job_id: str,
    status: str,
    *,
    result: dict | None = None,
    error: str | None = None,
    attempts: int | None = None,
) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.status = status
    job.result = result
    job.error = error
    if attempts is not None:
        job.attempts = attempts
    job.finished_at = _now()
    await session.commit()


async def reap_stale(session: AsyncSession, lease_seconds: int) -> list[dict]:
    """Requeue jobs stuck in ``running`` past the lease (a worker died mid-run)."""
    cutoff = _now() - timedelta(seconds=lease_seconds)
    rows = (
        await session.scalars(
            select(Job).where(Job.status == STATUS_RUNNING, Job.started_at < cutoff)
        )
    ).all()
    envelopes: list[dict] = []
    for job in rows:
        job.status = STATUS_QUEUED
        job.worker_id = None
        envelopes.append(envelope(job))
    if envelopes:
        await session.commit()
    return envelopes


async def deliver_webhook(
    url: str, payload: dict, *, secret: str = "", timeout: float = 10.0
) -> bool:
    """POST ``payload`` to ``url``, signed with HMAC-SHA256 when ``secret`` is set."""
    if not url:
        return False
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"content-type": "application/json", "user-agent": "browser-agent-toolkit"}
    if secret:
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["x-bat-signature"] = f"sha256={digest}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("webhook delivery to %s failed: %s", url, exc)
        return False
    if response.status_code >= 300:
        logger.warning("webhook %s returned %s", url, response.status_code)
        return False
    return True
