import fakeredis.aioredis

from app.queue import InProcessJobQueue, RedisJobQueue, build_queue


async def test_in_process_queue_roundtrip_and_timeout():
    queue = InProcessJobQueue()
    assert await queue.depth() == 0
    await queue.enqueue({"job_id": "1"})
    assert await queue.depth() == 1
    assert await queue.dequeue(1.0) == {"job_id": "1"}
    assert await queue.dequeue(0.05) is None


async def test_in_process_queue_preserves_order():
    queue = InProcessJobQueue()
    for name in ("a", "b", "c"):
        await queue.enqueue({"job_id": name})
    drained = [await queue.dequeue(1.0) for _ in range(3)]
    assert [item["job_id"] for item in drained] == ["a", "b", "c"]


async def test_redis_queue_roundtrip():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        queue = RedisJobQueue(redis)
        await queue.enqueue({"job_id": "x", "org_id": "o1"})
        assert await queue.depth() == 1
        assert await queue.dequeue(1.0) == {"job_id": "x", "org_id": "o1"}
        assert await queue.depth() == 0
    finally:
        await redis.aclose()


def test_build_queue_without_redis_is_in_process():
    assert isinstance(build_queue(None), InProcessJobQueue)
