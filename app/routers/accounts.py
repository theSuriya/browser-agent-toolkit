"""Self-serve signup and per-organization key management.

``POST /accounts/signup`` is public (a customer onboards themselves) and returns the
first API key exactly once. Everything else is scoped to the caller's organization so
one tenant can never see or manage another tenant's keys.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth import Principal, can_grant, require_admin, require_key
from app.config import get_settings
from app.db import get_session
from app.errors import AppError
from app.schemas import AccountInfo, KeyCreate, KeyCreated, KeyInfo, SignupRequest, SignupResponse

router = APIRouter(prefix="/accounts", tags=["accounts"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, session: Session) -> SignupResponse:
    """Create a user, an organization on the default plan, and a first API key."""
    settings = get_settings()
    user = await repository.get_or_create_user(session, payload.email.lower())
    org = await repository.create_organization(session, payload.org_name, plan_code=settings.default_plan)
    await repository.ensure_membership(session, org.id, user.id, role="owner")
    await repository.create_subscription(session, org.id, settings.default_plan)
    plaintext, record = await repository.create_api_key(
        session, org.id, name="default", scopes=["admin", "use"]
    )
    await session.commit()
    await repository.write_audit(
        session, org.id, actor=f"user:{user.id}", action="org.signup", detail={"plan": org.plan_code}
    )
    return SignupResponse(
        org_id=org.id, user_id=user.id, plan=org.plan_code, key=plaintext, key_id=record.id
    )


@router.get("/me", response_model=AccountInfo)
async def me(principal: Principal = Depends(require_key)) -> AccountInfo:
    return AccountInfo(
        org_id=principal.org_id,
        plan=principal.plan_code,
        key_id=principal.key_id,
        key_name=principal.name,
        scopes=sorted(principal.scopes),
        max_sessions_per_key=principal.max_sessions_per_key,
        max_agent_steps=principal.max_agent_steps,
    )


@router.post("/keys", response_model=KeyCreated, status_code=status.HTTP_201_CREATED)
async def create_org_key(
    payload: KeyCreate,
    session: Session,
    principal: Principal = Depends(require_admin),
) -> KeyCreated:
    """Mint an additional key for the caller's organization (admin scope required)."""
    # An org admin may not grant a scope they do not hold.
    requested = set(payload.scopes or ["use"])
    if not can_grant(principal.scopes, requested):
        raise AppError(403, "forbidden", "You cannot grant scopes you do not hold.")
    plaintext, record = await repository.create_api_key(
        session, principal.org_id, payload.name, scopes=list(requested), rate_limit=payload.rate_limit
    )
    await session.commit()
    await repository.write_audit(
        session, principal.org_id, actor=f"key:{principal.key_id}", action="key.create",
        detail={"key_id": record.id, "scopes": sorted(requested)},
    )
    return KeyCreated(
        id=record.id, name=record.name, key=plaintext, scopes=sorted(repository.text_to_scopes(record.scopes))
    )


@router.get("/keys", response_model=list[KeyInfo])
async def list_org_keys(
    session: Session,
    principal: Principal = Depends(require_admin),
) -> list[KeyInfo]:
    records = await repository.list_keys(session, principal.org_id)
    return [
        KeyInfo(
            id=r.id,
            name=r.name,
            scopes=sorted(repository.text_to_scopes(r.scopes)),
            rate_limit=r.rate_limit,
            created_at=r.created_at.timestamp(),
            revoked=r.revoked,
            last_used=r.last_used.timestamp() if r.last_used else None,
        )
        for r in records
    ]


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_org_key(
    key_id: str,
    session: Session,
    principal: Principal = Depends(require_admin),
) -> Response:
    if not await repository.revoke_key(session, principal.org_id, key_id):
        raise AppError(404, "not_found", "Unknown key id.")
    await repository.write_audit(
        session, principal.org_id, actor=f"key:{principal.key_id}", action="key.revoke",
        detail={"key_id": key_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
