"""Shared Redis client construction.

Phase 2 uses Redis for the global rate limit, the job queue and the session
registry. Everything degrades gracefully: when ``REDIS_URL`` is empty or the
server is unreachable the callers fall back to in-process behaviour, so a single
node still runs with no Redis at all.
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def make_redis(redis_url: str) -> Redis | None:
    """Build a lazily-connecting async Redis client, or ``None`` when unconfigured.

    ``Redis.from_url`` does not open a connection until the first command, so this is
    safe to call from synchronous startup code; an unavailable server surfaces later as
    an exception on first use, which the callers handle by falling back.
    """
    if not redis_url:
        return None
    return Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
