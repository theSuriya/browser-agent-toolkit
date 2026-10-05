"""Run the natural-language agent loop over HTTP."""

from fastapi import APIRouter, Depends

from app.agent import run_agent
from app.auth import Principal, require_key
from app.browser import manager
from app.schemas import AgentRequest, AgentResult

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/run", response_model=AgentResult)
async def run_agent_task(
    payload: AgentRequest,
    principal: Principal = Depends(require_key),
) -> AgentResult:
    """Drive a task. Pass ``session_id`` to continue in an existing session (it must
    belong to this key), or omit it to use a fresh session that is closed afterwards."""
    if payload.session_id:
        session = manager.get(payload.session_id, owner=principal.key_id)
        owns_session = False
    else:
        session = await manager.create_session(owner=principal.key_id)
        owns_session = True

    try:
        outcome = await run_agent(session, payload.task, max_steps=payload.max_steps)
    finally:
        if owns_session:
            await manager.close_session(session.id)

    return AgentResult(
        ok=bool(outcome.get("ok", False)),
        result=str(outcome.get("result", "")),
        steps=int(outcome.get("steps", 0)),
        session_id=session.id,
        transcript=list(outcome.get("transcript", [])),
    )
