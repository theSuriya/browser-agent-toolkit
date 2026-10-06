import pytest

import fakeredis.aioredis

from app.ratelimit import RedisSlidingWindowLimiter, SlidingWindowLimiter, build_limiter


async def test_in_memory_limiter_allows_up_to_limit_then_blocks():
    limiter = SlidingWindowLimiter(window_seconds=60)
    results = [await limiter.check("key", 3) for _ in range(4)]
    assert results == [True, True, True, False]


async def test_limit_of_zero_or_less_is_unlimited():
    limiter = SlidingWindowLimiter()
    assert await limiter.check("key", 0) is True
    assert await limiter.check("key", -5) is True


async def test_in_memory_limiter_keys_are_independent():
    limiter = SlidingWindowLimiter()
    assert await limiter.check("a", 1) is True
    assert await limiter.check("b", 1) is True
    assert await limiter.check("a", 1) is False


async def test_redis_limiter_enforces_the_limit_globally():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        limiter = RedisSlidingWindowLimiter(redis, window_seconds=60)
        results = [await limiter.check("tenant", 2) for _ in range(3)]
        assert results == [True, True, False]
    finally:
        await redis.aclose()


async def test_redis_limiter_falls_back_when_redis_is_down():
    class BrokenRedis:
        def pipeline(self, transaction=True):  # noqa: ARG002 - signature parity
            raise ConnectionError("redis down")

    limiter = RedisSlidingWindowLimiter(BrokenRedis(), window_seconds=60)
    results = [await limiter.check("tenant", 1) for _ in range(2)]
    assert results == [True, False]


def test_build_limiter_uses_in_memory_without_redis():
    assert isinstance(build_limiter(None), SlidingWindowLimiter)


def test_build_limiter_uses_redis_when_available():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = build_limiter(redis)
    assert isinstance(limiter, RedisSlidingWindowLimiter)
