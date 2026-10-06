"""Current-period usage and quota for the caller's organization."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import metering
from app.auth import Principal, require_key
from app.db import get_session
from app.schemas import UsageSummary

router = APIRouter(prefix="/usage", tags=["usage"])
Db = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=UsageSummary)
async def get_usage(
    db: Db,
    principal: Principal = Depends(require_key),
) -> UsageSummary:
    """Metered usage for the current month against the plan's included quota."""
    summary = await metering.usage_summary(db, principal.org_id, principal.plan_code)
    return UsageSummary.model_validate(summary)
