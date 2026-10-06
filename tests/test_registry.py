import fakeredis.aioredis

from app.registry import SessionRegistry


async def test_registry_tracks_lists_and_removes_sessions():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        registry = SessionRegistry(redis, ttl_seconds=60)
        await registry.register("s1", "orgA", worker_id="w1", url="http://a")
        await registry.register("s2", "orgA", worker_id="w2")

        listed = await registry.list_by_owner("orgA")
        assert {row["session_id"] for row in listed} == {"s1", "s2"}

        await registry.deregister("s1")
        remaining = await registry.list_by_owner("orgA")
        assert {row["session_id"] for row in remaining} == {"s2"}

        workers = await registry.workers()
        assert workers.get("w2") == 1
    finally:
        await redis.aclose()


async def test_touch_updates_url_and_last_seen():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        registry = SessionRegistry(redis, ttl_seconds=60)
        await registry.register("s1", "orgA", worker_id="w1")
        await registry.touch("s1", url="http://example.com")
        rows = await registry.list_by_owner("orgA")
        assert rows[0]["url"] == "http://example.com"
    finally:
        await redis.aclose()


async def test_owner_lists_are_isolated():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        registry = SessionRegistry(redis, ttl_seconds=60)
        await registry.register("s1", "orgA", worker_id="w1")
        await registry.register("s2", "orgB", worker_id="w1")
        assert {r["session_id"] for r in await registry.list_by_owner("orgA")} == {"s1"}
    finally:
        await redis.aclose()


async def test_registry_is_a_noop_without_redis():
    registry = SessionRegistry(None)
    await registry.register("s1", "orgA", worker_id="w1")
    assert registry.enabled is False
    assert await registry.list_by_owner("orgA") == []
    assert await registry.workers() == {}
