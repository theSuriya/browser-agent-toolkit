"""Session lifecycle and raw tool execution over HTTP.

Every session belongs to the API key that created it; other keys receive 404 for
it (see ``BrowserManager.get``).
"""

from fastapi import APIRouter, Depends, Query, Response, status

from app.auth import Principal, require_key
from app.browser import manager
from app.errors import AppError
from app.schemas import ActionRequest, SessionInfo, SessionOut
from app.tools import TOOL_NAMES, run_tool

router = APIRouter(prefix="/sessions", tags=["browser"])


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(principal: Principal = Depends(require_key)) -> SessionOut:
    session = await manager.create_session(owner=principal.key_id)
    return SessionOut(id=session.id)


@router.get("", response_model=list[SessionInfo])
async def list_sessions(principal: Principal = Depends(require_key)) -> list[SessionInfo]:
    return [SessionInfo(**info) for info in manager.list_sessions(owner=principal.key_id)]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, principal: Principal = Depends(require_key)) -> Response:
    session = manager.get(session_id, owner=principal.key_id)  # 404 unless it belongs to this key
    await manager.close_session(session.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{session_id}/actions")
async def run_action(
    session_id: str,
    payload: ActionRequest,
    principal: Principal = Depends(require_key),
) -> dict:
    # Validate the tool name before resolving the session, so an unknown tool is a
    # 400 regardless of the session id.
    if payload.tool not in TOOL_NAMES:
        raise AppError(400, "unknown_tool", f"Unknown tool '{payload.tool}'.")
    session = manager.get(session_id, owner=principal.key_id)
    return await run_tool(session, payload.tool, payload.arguments)


@router.get("/{session_id}/screenshot")
async def screenshot(
    session_id: str,
    full_page: bool = Query(default=False),
    principal: Principal = Depends(require_key),
) -> Response:
    session = manager.get(session_id, owner=principal.key_id)
    png = await session.page.screenshot(full_page=full_page)
    return Response(content=png, media_type="image/png")
