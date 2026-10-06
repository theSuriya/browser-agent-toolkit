"""Async job API: enqueue work and poll status. Execution happens in a worker."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import jobs as job_store
from app import metering
from app.auth import Principal, require_key
from app.db import get_session
from app.errors import AppError
from app.plans import get_plan
from app.schemas import AgentRequest, JobCreate, JobOut, JobPage, SiteCheckRequest

router = APIRouter(prefix="/jobs", tags=["jobs"])
Db = Annotated[AsyncSession, Depends(get_session)]
MAX_PAGE = 100


def _validated_input(payload: JobCreate) -> dict:
    """Reuse the existing request models so a job payload is validated identically."""
    model = AgentRequest if payload.kind == "agent" else SiteCheckRequest
    try:
        return model.model_validate(payload.input).model_dump()
    except ValidationError as exc:
        fields = ", ".join(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        raise AppError(422, "validation_failed", f"Invalid {payload.kind} input (check: {fields}).") from exc


@router.post("", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: JobCreate,
    request: Request,
    db: Db,
    principal: Principal = Depends(require_key),
) -> JobOut:
    plan = get_plan(principal.plan_code)
    validated = _validated_input(payload)
    if payload.kind == "agent":
        await metering.enforce_quota(db, principal.org_id, plan, "agent_tasks")
    else:
        await metering.enforce_quota(db, principal.org_id, plan, "sitecheck")
    await metering.enforce_quota(db, principal.org_id, plan, "browser_seconds")

    job = await job_store.create_job(
        db,
        org_id=principal.org_id,
        key_id=principal.key_id,
        kind=payload.kind,
        payload=validated,
        callback_url=payload.callback_url,
    )
    await request.app.state.queue.enqueue(job_store.envelope(job))
    return JobOut.from_job(job)


@router.get("", response_model=JobPage)
async def list_jobs(
    db: Db,
    principal: Principal = Depends(require_key),
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobPage:
    rows, next_offset = await job_store.list_jobs(db, principal.org_id, limit, offset)
    return JobPage(jobs=[JobOut.from_job(row) for row in rows], next_offset=next_offset)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    db: Db,
    principal: Principal = Depends(require_key),
) -> JobOut:
    job = await job_store.get_job(db, principal.org_id, job_id)
    if job is None:
        raise AppError(404, "not_found", "Unknown job id.")
    return JobOut.from_job(job)
