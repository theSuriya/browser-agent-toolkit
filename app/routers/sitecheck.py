"""Website test harness over HTTP, with quota enforcement and metering."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import metering
from app.auth import Principal, require_key
from app.browser import manager
from app.db import get_session
from app.plans import get_plan
from app.schemas import SiteCheckRequest
from app.sitecheck import run_site_check

router = APIRouter(prefix="/sitecheck", tags=["sitecheck"])
Db = Annotated[AsyncSession, Depends(get_session)]


@router.post("")
async def check_site(
    payload: SiteCheckRequest,
    db: Db,
    principal: Principal = Depends(require_key),
) -> dict:
    """Load a URL in a fresh browser session, run the checks, and close it."""
    plan = get_plan(principal.plan_code)
    await metering.enforce_quota(db, principal.org_id, plan, "sitecheck")
    await metering.enforce_quota(db, principal.org_id, plan, "browser_seconds")

    session = await manager.create_session(
        owner=principal.key_id, max_per_owner=principal.max_sessions_per_key
    )
    started = time.monotonic()
    try:
        result = await run_site_check(
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

    elapsed = time.monotonic() - started
    await metering.meter(db, principal.org_id, "sitecheck", 1, key_id=principal.key_id)
    await metering.meter(db, principal.org_id, "browser_seconds", elapsed, key_id=principal.key_id)
    if payload.screenshot:
        await metering.meter(db, principal.org_id, "screenshots", 1, key_id=principal.key_id)
    return result
