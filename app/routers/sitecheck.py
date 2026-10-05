"""Website test harness over HTTP."""

from fastapi import APIRouter, Depends

from app.auth import Principal, require_key
from app.browser import manager
from app.schemas import SiteCheckRequest
from app.sitecheck import run_site_check

router = APIRouter(prefix="/sitecheck", tags=["sitecheck"])


@router.post("")
async def check_site(payload: SiteCheckRequest, principal: Principal = Depends(require_key)) -> dict:
    """Load a URL in a fresh browser session, run the checks, and close it."""
    session = await manager.create_session(owner=principal.key_id)
    try:
        return await run_site_check(
            session,
            payload.url,
            required_selectors=payload.required_selectors,
            required_text=payload.required_text,
            check_links=payload.check_links,
            max_links=payload.max_links,
            screenshot=payload.screenshot,
        )
    finally:
        await manager.close_session(session.id)
