"""Cross-instance session registry backed by Redis.

Browser sessions live in the process that created them, but their metadata is
published to Redis so any API instance can report what a tenant has open, which
worker owns a session, and reap leaked records. Redis is optional: without it the
registry is a no-op and the in-process browser manager stays the only source of
truth.
"""

from __future__ import annotations

import time

DEFAULT_TTL_SECONDS = 1800


class SessionRegistry:
    def __init__(self, redis, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"bat:sess:{session_id}"

    @staticmethod
    def _owner_key(owner: str) -> str:
        return f"bat:owner:{owner}"

    async def register(self, session_id: str, owner: str, *, worker_id: str, url: str = "") -> None:
        if self._redis is None:
            return
        record = {
            "session_id": session_id,
            "owner": owner,
            "worker_id": worker_id,
            "url": url,
            "updated": time.time(),
        }
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(self._session_key(session_id), mapping=record)
            pipe.expire(self._session_key(session_id), self._ttl)
            pipe.sadd(self._owner_key(owner), session_id)
            pipe.expire(self._owner_key(owner), self._ttl)
            await pipe.execute()

    async def touch(self, session_id: str, url: str | None = None) -> None:
        if self._redis is None or not await self._redis.exists(self._session_key(session_id)):
            return
        mapping: dict[str, object] = {"updated": time.time()}
        if url is not None:
            mapping["url"] = url
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(self._session_key(session_id), mapping=mapping)
            pipe.expire(self._session_key(session_id), self._ttl)
            await pipe.execute()

    async def deregister(self, session_id: str) -> None:
        if self._redis is None:
            return
        record = await self._redis.hgetall(self._session_key(session_id))
        async with self._redis.pipeline(transaction=True) as pipe:
            if record.get("owner"):
                pipe.srem(self._owner_key(record["owner"]), session_id)
            pipe.delete(self._session_key(session_id))
            await pipe.execute()

    async def list_by_owner(self, owner: str) -> list[dict]:
        if self._redis is None:
            return []
        session_ids = await self._redis.smembers(self._owner_key(owner))
        records: list[dict] = []
        stale: list[str] = []
        for session_id in session_ids:
            record = await self._redis.hgetall(self._session_key(session_id))
            if not record:
                stale.append(session_id)
                continue
            records.append(record)
        if stale:
            await self._redis.srem(self._owner_key(owner), *stale)
        records.sort(key=lambda row: float(row.get("updated", 0)), reverse=True)
        return records

    async def workers(self) -> dict[str, int]:
        """Return how many live sessions each worker process currently holds."""
        if self._redis is None:
            return {}
        counts: dict[str, int] = {}
        async for key in self._redis.scan_iter(match="bat:sess:*"):
            record = await self._redis.hgetall(key)
            worker_id = record.get("worker_id", "unknown")
            counts[worker_id] = counts.get(worker_id, 0) + 1
        return counts
