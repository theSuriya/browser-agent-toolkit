"""Per-key sliding-window rate limiting (in-process).

Counts requests in a rolling window so a runaway client cannot monopolise the
browser pool. With multiple worker processes each worker enforces its own share;
use a shared store (Redis) if you need an exact global limit.
"""

import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int) -> bool:
        """Record a request for ``key``. Return ``False`` when ``limit`` is exceeded."""
        if limit <= 0:  # 0 or negative means "no limit"
            return True
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
