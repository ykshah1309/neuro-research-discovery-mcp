"""Simple asyncio token-bucket rate limiter.

One bucket per upstream client. `await bucket.acquire()` blocks until a token is
available. Bursts up to `burst` are allowed; long-run rate converges to `rate_per_sec`.
"""

from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    def __init__(self, rate_per_sec: float, burst: int | None = None) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._rate = float(rate_per_sec)
        self._capacity = float(burst) if burst is not None else max(1.0, float(rate_per_sec))
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self._rate
            # Sleep outside the lock so other waiters can also check when tokens arrive.
            await asyncio.sleep(wait)
