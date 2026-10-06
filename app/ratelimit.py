"""Rate limiting.

``SlidingWindowLimiter`` is per-process and exact within a single worker.
``RedisSlidingWindowLimiter`` is the global limit shared by every API instance; if
Redis becomes unreachable it degrades to the in-process limiter instead of failing
the request (a paid API should keep serving under Redis trouble).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Protocol

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int) -> bool: ...


class SlidingWindowLimiter:
    """Per-process sliding window. Not global; use the Redis limiter for that."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    async def check(self, key: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


class RedisSlidingWindowLimiter:
    """Global sliding window backed by a Redis sorted set, with an in-process fallback."""

    def __init__(self, redis, window_seconds: float = 60.0) -> None:
        self._redis = redis
        self._window = window_seconds
        self._fallback = SlidingWindowLimiter(window_seconds)

    async def check(self, key: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = time.time()
        redis_key = f"bat:rl:{key}"
        # A unique member per hit so concurrent requests never collide on the score.
        member = f"{now:.6f}-{uuid.uuid4().hex}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(redis_key, 0, now - self._window)
                pipe.zadd(redis_key, {member: now})
                pipe.zcard(redis_key)
                pipe.expire(redis_key, int(self._window) + 1)
                _, _, count, _ = await pipe.execute()
            if count > limit:
                await self._redis.zrem(redis_key, member)
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - never fail a request because Redis hiccuped
            logger.warning("Redis rate limit unavailable (%s); using in-process fallback", exc)
            return await self._fallback.check(key, limit)


def build_limiter(redis, window_seconds: float = 60.0) -> RateLimiter:
    if redis is None:
        return SlidingWindowLimiter(window_seconds)
    return RedisSlidingWindowLimiter(redis, window_seconds)
