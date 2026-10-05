"""Session lifecycle and raw tool execution over HTTP."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.browser import manager
from app.errors import AppError
from app.schemas import ActionRequest, SessionInfo, SessionOut
from app.tools import TOOL_NAMES, run_tool

router = APIRouter(prefix="/sessions", tags=["browser"])


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session() -> SessionOut:
    session = await manager.create_session()
    return SessionOut(id=session.id)


@router.get("", response_model=list[SessionInfo])
async def list_sessions() -> list[SessionInfo]:
    return [SessionInfo(**item) for item in manager.list_sessions()]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str) -> Response:
    manager.get(session_id)  # raises 404 for an unknown id
    await manager.close_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{session_id}/actions")
async def run_action(session_id: str, payload: ActionRequest) -> dict:
    if payload.tool not in TOOL_NAMES:
        raise AppError(400, "unknown_tool", f"'{payload.tool}' is not a known tool.")
    session = manager.get(session_id)
    return await run_tool(session, payload.tool, payload.arguments)


@router.get("/{session_id}/console")
async def get_console(session_id: str) -> dict:
    session = manager.get(session_id)
    return await run_tool(session, "get_console", {})


@router.get("/{session_id}/screenshot")
async def screenshot(session_id: str, full_page: bool = False) -> Response:
    session = manager.get(session_id)
    png = await session.page.screenshot(full_page=full_page)
    return Response(content=png, media_type="image/png")
