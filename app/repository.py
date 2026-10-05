"""Owner-scoped data access for keys, tenancy, metering and audit.

Every function takes an :class:`AsyncSession` and is called from routers, the auth
store or background code. Queries that touch a tenant's data always filter by
``org_id`` so one organization can never read or revoke another's resources.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, AuditLog, Organization, OrgMember, Plan, Subscription, UsageEvent, User
from app.plans import PLANS


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return "bat_" + secrets.token_urlsafe(32)


def scopes_to_text(scopes) -> str:
    return ",".join(sorted(set(scopes or ["use"])))


def text_to_scopes(value: str) -> frozenset[str]:
    return frozenset(s for s in (value or "").split(",") if s)


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# -- Identity and tenancy ---------------------------------------------------
async def get_or_create_user(session: AsyncSession, email: str) -> User:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return existing
    user = User(email=email)
    session.add(user)
    await session.flush()
    return user


async def create_organization(session: AsyncSession, name: str, plan_code: str = "free") -> Organization:
    org = Organization(name=name, plan_code=plan_code)
    session.add(org)
    await session.flush()
    return org


async def ensure_membership(session: AsyncSession, org_id: str, user_id: str, role: str = "owner") -> OrgMember:
    existing = await session.get(OrgMember, (org_id, user_id))
    if existing is not None:
        return existing
    member = OrgMember(org_id=org_id, user_id=user_id, role=role)
    session.add(member)
    await session.flush()
    return member


async def create_subscription(
    session: AsyncSession, org_id: str, plan_code: str, status: str = "active"
) -> Subscription:
    sub = Subscription(org_id=org_id, plan_code=plan_code, status=status)
    session.add(sub)
    await session.flush()
    return sub


async def get_org(session: AsyncSession, org_id: str) -> Organization | None:
    return await session.get(Organization, org_id)


# -- API keys ---------------------------------------------------------------
async def create_api_key(
    session: AsyncSession,
    org_id: str,
    name: str,
    scopes: list[str] | None = None,
    rate_limit: int | None = None,
) -> tuple[str, ApiKey]:
    plaintext = generate_key()
    record = ApiKey(
        org_id=org_id,
        name=name,
        key_hash=hash_key(plaintext),
        scopes=scopes_to_text(scopes),
        rate_limit=rate_limit,
    )
    session.add(record)
    await session.flush()
    return plaintext, record


async def authenticate_key(session: AsyncSession, token: str) -> tuple[ApiKey, str] | None:
    """Return ``(key, plan_code)`` for a valid token, else ``None``.

    Persists ``last_used`` so usage survives restarts (previously in-memory only).
    """
    record = await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(token)))
    if record is None or record.revoked:
        return None
    org = await session.get(Organization, record.org_id)
    plan_code = org.plan_code if org is not None else "free"
    await session.execute(
        update(ApiKey).where(ApiKey.id == record.id).values(last_used=datetime.now(timezone.utc))
    )
    await session.commit()
    return record, plan_code


async def revoke_key(session: AsyncSession, org_id: str, key_id: str) -> bool:
    """Revoke a key, scoped to its organization. Returns False when nothing matched."""
    result = await session.execute(
        update(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == org_id).values(revoked=True)
    )
    await session.commit()
    return result.rowcount > 0


async def list_keys(session: AsyncSession, org_id: str) -> list[ApiKey]:
    rows = await session.scalars(
        select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at, ApiKey.id)
    )
    return list(rows)


# -- Bootstrap and plans ----------------------------------------------------
async def ensure_bootstrap(
    session: AsyncSession, keys: list[str], org_name: str = "bootstrap", rate_limit: int | None = None
) -> Organization:
    """Create the admin organization and insert any bootstrap keys not yet present.

    Returns the bootstrap organization. Bootstrap keys carry an explicit rate limit so
    the global ``RATE_LIMIT_PER_MIN`` ceiling still applies to them.
    """
    org = await session.scalar(
        select(Organization).where(Organization.name == org_name, Organization.plan_code == "enterprise")
    )
    if org is None:
        org = await create_organization(session, org_name, plan_code="enterprise")
    for key in keys:
        if not key:
            continue
        digest = hash_key(key)
        exists = await session.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
        if exists is not None:
            continue
        session.add(
            ApiKey(
                org_id=org.id,
                name="bootstrap",
                key_hash=digest,
                scopes=scopes_to_text(["admin"]),
                rate_limit=rate_limit,
            )
        )
    await session.commit()
    return org


async def seed_plans(session: AsyncSession) -> int:
    """Upsert the plan catalog into the ``plans`` table. Returns rows written."""
    written = 0
    for limits in PLANS.values():
        existing = await session.scalar(select(Plan).where(Plan.code == limits.code))
        if existing is None:
            session.add(
                Plan(
                    code=limits.code,
                    price_cents=limits.price_cents,
                    included_browser_seconds=limits.included_browser_seconds,
                    included_agent_tasks=limits.included_agent_tasks,
                    included_sitechecks=limits.included_sitechecks,
                    overage_cents_per_browser_sec=limits.overage_cents_per_browser_sec,
                    overage_cents_per_agent_task=limits.overage_cents_per_agent_task,
                )
            )
        else:
            existing.price_cents = limits.price_cents
            existing.included_browser_seconds = limits.included_browser_seconds
            existing.included_agent_tasks = limits.included_agent_tasks
            existing.included_sitechecks = limits.included_sitechecks
            existing.overage_cents_per_browser_sec = limits.overage_cents_per_browser_sec
            existing.overage_cents_per_agent_task = limits.overage_cents_per_agent_task
        written += 1
    await session.commit()
    return written


# -- Metering ---------------------------------------------------------------
async def record_usage(
    session: AsyncSession,
    org_id: str,
    metric: str,
    quantity: float,
    key_id: str | None = None,
    job_id: str | None = None,
) -> UsageEvent:
    event = UsageEvent(org_id=org_id, key_id=key_id, job_id=job_id, metric=metric, quantity=float(quantity))
    session.add(event)
    await session.commit()
    return event


async def usage_since(session: AsyncSession, org_id: str, since: datetime) -> dict[str, float]:
    rows = await session.execute(
        select(UsageEvent.metric, func.coalesce(func.sum(UsageEvent.quantity), 0.0))
        .where(UsageEvent.org_id == org_id, UsageEvent.created_at >= since)
        .group_by(UsageEvent.metric)
    )
    return {metric: float(total) for metric, total in rows.all()}


async def write_audit(
    session: AsyncSession, org_id: str, actor: str, action: str, detail: dict | None = None
) -> None:
    session.add(AuditLog(org_id=org_id, actor=actor, action=action, detail=detail or {}))
    await session.commit()
