"""Run the natural-language agent loop over HTTP, with quota enforcement and metering."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import metering
from app.agent import run_agent
from app.auth import Principal, require_key
from app.browser import manager
from app.db import get_session
from app.plans import get_plan
from app.schemas import AgentRequest, AgentResult

router = APIRouter(prefix="/agents", tags=["agents"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/run", response_model=AgentResult)
async def run_agent_task(
    payload: AgentRequest,
    db: Session,
    principal: Principal = Depends(require_key),
) -> AgentResult:
    """Drive a task. Pass ``session_id`` to continue in an existing session (it must
    belong to this key), or omit it to use a fresh session that is closed afterwards.

    Enforces the plan's agent-task and browser-seconds quotas, caps steps at the plan
    limit, and meters browser-seconds, the task, and the steps it used.
    """
    plan = get_plan(principal.plan_code)
    await metering.enforce_quota(db, principal.org_id, plan, "agent_tasks")
    await metering.enforce_quota(db, principal.org_id, plan, "browser_seconds")
    max_steps = min(payload.max_steps, principal.max_agent_steps)

    if payload.session_id:
        browser_session = manager.get(payload.session_id, owner=principal.key_id)
        owns_session = False
    else:
        browser_session = await manager.create_session(
            owner=principal.key_id, max_per_owner=principal.max_sessions_per_key
        )
        owns_session = True

    started = time.monotonic()
    try:
        outcome = await run_agent(browser_session, payload.task, max_steps=max_steps)
    finally:
        if owns_session:
            await manager.close_session(browser_session.id)

    elapsed = time.monotonic() - started
    steps = int(outcome.get("steps", 0))
    await metering.meter(db, principal.org_id, "browser_seconds", elapsed, key_id=principal.key_id)
    await metering.meter(db, principal.org_id, "agent_tasks", 1, key_id=principal.key_id)
    await metering.meter(db, principal.org_id, "agent_steps", steps, key_id=principal.key_id)

    return AgentResult(
        ok=bool(outcome.get("ok", False)),
        result=str(outcome.get("result", "")),
        steps=steps,
        session_id=browser_session.id,
        transcript=list(outcome.get("transcript", [])),
    )
