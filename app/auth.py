"""API-key authentication.

Keys are stored only as SHA-256 hashes; the plaintext is shown once, at creation.
An in-memory index makes verification a dictionary lookup (no I/O per request),
while SQLite provides persistence across restarts.

The authenticated caller is represented by a :class:`Principal`. Middleware sets
it for the current request (so both the REST routes and the mounted MCP endpoint
see the same identity); the FastAPI dependencies here read it back.
"""

import hashlib
import secrets
import sqlite3
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, Request

from app.config import get_settings
from app.errors import AppError


@dataclass(frozen=True)
class Principal:
    key_id: str
    name: str
    scopes: frozenset[str]
    rate_limit: int | None = None

    @property
    def is_admin(self) -> bool:
        return "admin" in self.scopes


# Used only when REQUIRE_AUTH is off (local development and tests). It is an
# admin so every endpoint is reachable without a key.
ANONYMOUS = Principal(key_id="local", name="local", scopes=frozenset({"admin"}), rate_limit=None)

_current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


def set_current_principal(principal: Principal | None):
    return _current_principal.set(principal)


def reset_current_principal(token) -> None:
    _current_principal.reset(token)


def get_current_principal() -> Principal | None:
    return _current_principal.get()


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass
class KeyRecord:
    id: str
    name: str
    key_hash: str
    scopes: frozenset[str]
    rate_limit: int | None
    created_at: float
    revoked: bool = False
    last_used: float | None = None


class KeyStore:
    """SQLite-backed store of hashed API keys with an in-memory index."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._by_hash: dict[str, KeyRecord] = {}
        self._by_id: dict[str, KeyRecord] = {}
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._load()

    # -- persistence -------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    scopes TEXT NOT NULL,
                    rate_limit INTEGER,
                    created_at REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _load(self) -> None:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM api_keys").fetchall()
        for row in rows:
            record = KeyRecord(
                id=row["id"],
                name=row["name"],
                key_hash=row["key_hash"],
                scopes=frozenset(s for s in row["scopes"].split(",") if s),
                rate_limit=row["rate_limit"],
                created_at=row["created_at"],
                revoked=bool(row["revoked"]),
            )
            self._by_hash[record.key_hash] = record
            self._by_id[record.id] = record

    def _insert(self, record: KeyRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys (id, name, key_hash, scopes, rate_limit, created_at, revoked)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.name,
                    record.key_hash,
                    ",".join(sorted(record.scopes)),
                    record.rate_limit,
                    record.created_at,
                    int(record.revoked),
                ),
            )

    def _set_revoked(self, key_id: str, revoked: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE api_keys SET revoked = ? WHERE id = ?", (int(revoked), key_id))

    # -- api ---------------------------------------------------------------
    def ensure_bootstrap(self, keys: list[str]) -> int:
        """Insert any bootstrap keys that are not already present. Returns the count added."""
        added = 0
        with self._lock:
            for key in keys:
                if not key:
                    continue
                key_hash = _hash(key)
                if key_hash in self._by_hash:
                    continue
                record = KeyRecord(
                    id=secrets.token_hex(8),
                    name="bootstrap",
                    key_hash=key_hash,
                    scopes=frozenset({"admin"}),
                    rate_limit=None,
                    created_at=time.time(),
                )
                self._insert(record)
                self._by_hash[key_hash] = record
                self._by_id[record.id] = record
                added += 1
        return added

    def verify(self, api_key: str) -> Principal | None:
        record = self._by_hash.get(_hash(api_key))
        if record is None or record.revoked:
            return None
        record.last_used = time.time()
        return Principal(
            key_id=record.id,
            name=record.name,
            scopes=record.scopes,
            rate_limit=record.rate_limit,
        )

    def mint(
        self,
        name: str,
        scopes: list[str] | None = None,
        rate_limit: int | None = None,
    ) -> tuple[str, KeyRecord]:
        """Create a key. Returns ``(plaintext, record)``; the plaintext is not stored."""
        plaintext = "bat_" + secrets.token_urlsafe(32)
        record = KeyRecord(
            id=secrets.token_hex(8),
            name=name,
            key_hash=_hash(plaintext),
            scopes=frozenset(scopes or ["use"]),
            rate_limit=rate_limit,
            created_at=time.time(),
        )
        with self._lock:
            self._insert(record)
            self._by_hash[record.key_hash] = record
            self._by_id[record.id] = record
        return plaintext, record

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            record = self._by_id.get(key_id)
            if record is None:
                return False
            record.revoked = True
            self._set_revoked(key_id, True)
            return True

    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            records = sorted(self._by_id.values(), key=lambda r: r.created_at)
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "scopes": sorted(r.scopes),
                    "rate_limit": r.rate_limit,
                    "created_at": r.created_at,
                    "revoked": r.revoked,
                    "last_used": r.last_used,
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
        resolved = get_key_store(request).verify(raw)
        if resolved is not None:
            return resolved
    if not get_settings().require_auth:
        return ANONYMOUS
    raise AppError(401, "unauthenticated", "Provide a valid API key: Authorization: Bearer <key>.")


async def require_admin(principal: Principal = Depends(require_key)) -> Principal:
    if not principal.is_admin:
        raise AppError(403, "forbidden", "This endpoint requires an admin key.")
    return principal
