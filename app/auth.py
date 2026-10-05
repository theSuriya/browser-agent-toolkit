"""API-key authentication backed by the database.

Keys are stored only as SHA-256 hashes; the plaintext is shown once, at creation.
Verification reads the ``api_keys`` table (persisting ``last_used`` so usage survives
restarts) and resolves the owning organization's plan, which the request pipeline uses
to enforce rate limits, session caps and quotas.

The authenticated caller is a :class:`Principal`. Middleware sets it for the current
request (so both the REST routes and the mounted MCP endpoint see the same identity);
the FastAPI dependencies here read it back.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import repository
from app.config import get_settings
from app.errors import AppError
from app.plans import get_plan


@dataclass(frozen=True)
class Principal:
    key_id: str
    name: str
    scopes: frozenset[str]
    rate_limit: int | None = None
    org_id: str = "local"
    plan_code: str = "free"
    max_sessions_per_key: int = 4
    max_agent_steps: int = 25

    @property
    def is_admin(self) -> bool:
        return "admin" in self.scopes

    @classmethod
    def from_key(cls, record, plan_code: str) -> "Principal":
        plan = get_plan(plan_code)
        return cls(
            key_id=record.id,
            name=record.name,
            scopes=repository.text_to_scopes(record.scopes),
            rate_limit=record.rate_limit,
            org_id=record.org_id,
            plan_code=plan.code,
            max_sessions_per_key=plan.max_sessions_per_key,
            max_agent_steps=plan.max_agent_steps,
        )


# Used only when REQUIRE_AUTH is off (local development and tests). It is an admin
# with the enterprise plan so every endpoint is reachable and effectively uncapped.
ANONYMOUS = Principal(
    key_id="local",
    name="local",
    scopes=frozenset({"admin"}),
    rate_limit=None,
    org_id="local",
    plan_code="enterprise",
    max_sessions_per_key=100,
    max_agent_steps=100,
)

_current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


def set_current_principal(principal: Principal | None):
    return _current_principal.set(principal)


def reset_current_principal(token) -> None:
    _current_principal.reset(token)


def get_current_principal() -> Principal | None:
    return _current_principal.get()


# A scope grants the scopes it implies: "admin" also carries "use".
_SCOPE_IMPLIES: dict[str, set[str]] = {"admin": {"admin", "use"}}

# Every scope a caller may hand out, given the scopes they hold.
_SCOPE_CEILING: frozenset[str] = frozenset({"admin", "use"})


def can_grant(caller_scopes: frozenset[str], requested: set[str]) -> bool:
    """True when a caller holding ``caller_scopes`` may grant ``requested``."""
    if not requested <= _SCOPE_CEILING:
        return False
    expanded: set[str] = set()
    for scope in caller_scopes:
        expanded |= _SCOPE_IMPLIES.get(scope, {scope})
    return requested <= expanded


class KeyStore:
    """Database-backed key store. Holds a session factory so middleware can use it."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    def _session(self) -> AsyncSession:
        return self._sessionmaker()

    async def verify(self, api_key: str | None) -> Principal | None:
        if not api_key:
            return None
        async with self._session() as session:
            resolved = await repository.authenticate_key(session, api_key)
        if resolved is None:
            return None
        record, plan_code = resolved
        return Principal.from_key(record, plan_code)

    async def ensure_bootstrap(self, keys: list[str]) -> str:
        """Idempotently create the bootstrap org + keys; returns the org id."""
        async with self._session() as session:
            org = await repository.ensure_bootstrap(
                session, keys, rate_limit=get_settings().rate_limit_per_min
            )
            return org.id

    async def bootstrap_org_id(self) -> str:
        return await self.ensure_bootstrap([])

    async def mint(
        self,
        name: str,
        scopes: list[str] | None = None,
        rate_limit: int | None = None,
        org_id: str | None = None,
    ) -> tuple[str, repository.ApiKey]:
        """Create a key. Returns ``(plaintext, record)``; the plaintext is not stored."""
        async with self._session() as session:
            if org_id is None:
                org = await repository.ensure_bootstrap(session, [])
                org_id = org.id
            plaintext, record = await repository.create_api_key(
                session, org_id, name, scopes=scopes, rate_limit=rate_limit
            )
            await session.commit()
            return plaintext, record

    async def revoke(self, org_id: str, key_id: str) -> bool:
        async with self._session() as session:
            return await repository.revoke_key(session, org_id, key_id)

    async def list_public(self, org_id: str) -> list[dict]:
        async with self._session() as session:
            records = await repository.list_keys(session, org_id)
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "scopes": sorted(repository.text_to_scopes(r.scopes)),
                    "rate_limit": r.rate_limit,
                    "created_at": r.created_at.timestamp(),
                    "revoked": r.revoked,
                    "last_used": r.last_used.timestamp() if r.last_used else None,
                }
                for r in records
            ]


# -- FastAPI integration ---------------------------------------------------
def get_key_store(request: Request) -> KeyStore:
    store = getattr(request.app.state, "key_store", None)
    if store is None:  # pragma: no cover - only if create_app did not set it
        raise AppError(500, "server_error", "Key store is not initialised.")
    return store


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


async def require_key(request: Request) -> Principal:
    """Resolve the caller's API key to a Principal (401 when missing or invalid)."""
    principal = get_current_principal()
    if principal is not None:
        return principal
    raw = _bearer(request)
    if raw:
        resolved = await get_key_store(request).verify(raw)
        if resolved is not None:
            return resolved
    if not get_settings().require_auth:
        return ANONYMOUS
    raise AppError(401, "unauthenticated", "Provide a valid API key: Authorization: Bearer <key>.")


async def require_admin(principal: Principal = Depends(require_key)) -> Principal:
    if not principal.is_admin:
        raise AppError(403, "forbidden", "This endpoint requires an admin key.")
    return principal
