"""Job queue: Redis-backed across instances, in-process for one node or tests.

Both implementations expose the same tiny interface so the API and the worker do
not care which one is wired in. ``build_queue`` picks Redis when a client is
available and the in-process queue otherwise.
"""

from __future__ import annotations

import asyncio
import json
from typing import Protocol


class JobQueue(Protocol):
    async def enqueue(self, job: dict) -> None: ...
    async def dequeue(self, timeout: float) -> dict | None: ...
    async def depth(self) -> int: ...


class InProcessJobQueue:
    """A single-process queue; work only flows to a worker in the same process."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    async def enqueue(self, job: dict) -> None:
        await self._queue.put(job)

    async def dequeue(self, timeout: float) -> dict | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def depth(self) -> int:
        return self._queue.qsize()


class RedisJobQueue:
    """A global FIFO queue any worker process can consume from."""

    KEY = "bat:jobs"

    def __init__(self, redis) -> None:
        self._redis = redis

    async def enqueue(self, job: dict) -> None:
        await self._redis.lpush(self.KEY, json.dumps(job, separators=(",", ":")))

    async def dequeue(self, timeout: float) -> dict | None:
        item = await self._redis.brpop(self.KEY, timeout=max(1, int(timeout)))
        if item is None:
            return None
        _, payload = item
        return json.loads(payload)

    async def depth(self) -> int:
        return int(await self._redis.llen(self.KEY))


def build_queue(redis) -> JobQueue:
    if redis is None:
        return InProcessJobQueue()
    return RedisJobQueue(redis)
