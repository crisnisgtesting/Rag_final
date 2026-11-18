"""In-process sliding-window rate limiting.

The initial deployment is single-host without Redis. This limiter protects the
login endpoint per process; it is intentionally small and replaceable later.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        async with self._lock:
            events = self._events[key]
            self._prune(events, now)
            if len(events) >= self.max_attempts:
                retry_after = max(1, int(events[0] + self.window_seconds - now) + 1)
                return False, retry_after
            return True, 0

    async def record_failure(self, key: str) -> None:
        now = time.monotonic()
        async with self._lock:
            events = self._events[key]
            self._prune(events, now)
            events.append(now)

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._events.pop(key, None)

    def _prune(self, events: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
