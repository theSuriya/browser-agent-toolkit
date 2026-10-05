"""Run the natural-language agent loop over HTTP."""

from __future__ import annotations

from fastapi import APIRouter

from app.agent import run_agent
from app.browser import manager
from app.schemas import AgentRequest, AgentResult

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/run", response_model=AgentResult)
async def run_agent_task(payload: AgentRequest) -> AgentResult:
    """Run a task. Pass session_id to continue in an existing session, or omit it
    to use a fresh, automatically closed session."""
    if payload.session_id:
        session = manager.get(payload.session_id)
        owns_session = False
    else:
        session = await manager.create_session()
        owns_session = True

    try:
        outcome = await run_agent(session, payload.task, max_steps=payload.max_steps)
    finally:
        if owns_session:
            await manager.close_session(session.id)

    return AgentResult(
        ok=outcome.get("ok", False),
        result=outcome.get("result", ""),
        steps=outcome.get("steps", 0),
        session_id=session.id,
        transcript=outcome.get("transcript", []),
    )
