"""Async TTL cache with per-key lock to prevent thundering-herd on cold misses.

Per-call cache stats:
    `cache_stats` is a contextvars.ContextVar holding a dict
    `{"hits": int, "misses": int}`. The server layer sets it at the start of
    every tool call and reads it at the end for audit logging. When unset,
    cache lookups don't touch it, so library users outside the MCP server pay
    no overhead.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from cachetools import TTLCache

T = TypeVar("T")

cache_stats: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "cache_stats", default=None,
)


def _record_hit() -> None:
    stats = cache_stats.get()
    if stats is not None:
        stats["hits"] = stats.get("hits", 0) + 1


def _record_miss() -> None:
    stats = cache_stats.get()
    if stats is not None:
        stats["misses"] = stats.get("misses", 0) + 1


class AsyncTTLCache:
    """A wrapper around cachetools.TTLCache with asyncio locks per key."""

    def __init__(self, maxsize: int = 1024, ttl: float = 3600.0) -> None:
        self._cache: TTLCache[str, Any] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        cached = self._cache.get(key, _MISS)
        if cached is not _MISS:
            _record_hit()
            return cached  # type: ignore[return-value]
        lock = await self._lock_for(key)
        async with lock:
            cached = self._cache.get(key, _MISS)
            if cached is not _MISS:
                # Another caller raced us to fill the cache while we waited.
                # That's still a hit from the user's perspective.
                _record_hit()
                return cached  # type: ignore[return-value]
            _record_miss()
            value = await factory()
            self._cache[key] = value
            return value

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._cache


_MISS = object()


def make_key(*parts: Any) -> str:
    """Stable cache key from positional args. Sorts dict keys for determinism."""
    serializable = [_canonical(p) for p in parts]
    blob = json.dumps(serializable, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def async_cached(cache: AsyncTTLCache, key_prefix: str) -> Callable:
    """Decorator: cache an async function's result, keyed by (prefix, args, kwargs)."""
    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Skip `self` if present — it isn't stable across instances and clutters keys.
            cache_args = args[1:] if args and not isinstance(args[0], (str, int, float, bool, type(None), dict, list, tuple)) else args
            key = make_key(key_prefix, cache_args, kwargs)
            return await cache.get_or_set(key, lambda: fn(*args, **kwargs))
        return wrapper
    return decorator
