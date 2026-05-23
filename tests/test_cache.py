"""Tests for the async TTL cache + thundering-herd protection."""

from __future__ import annotations

import asyncio

import pytest

from neuro_research_discovery.cache import AsyncTTLCache, make_key


@pytest.mark.asyncio
async def test_cache_returns_cached_value_on_second_call():
    cache = AsyncTTLCache(maxsize=8, ttl=60.0)
    counter = {"n": 0}

    async def factory():
        counter["n"] += 1
        return counter["n"]

    a = await cache.get_or_set("k", factory)
    b = await cache.get_or_set("k", factory)
    assert a == b == 1
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_cache_collapses_concurrent_misses_to_one_factory_call():
    """Thundering-herd protection: 10 concurrent get_or_set on a cold key should
    only invoke the factory once."""
    cache = AsyncTTLCache(maxsize=8, ttl=60.0)
    counter = {"n": 0}

    async def slow_factory():
        await asyncio.sleep(0.05)
        counter["n"] += 1
        return "value"

    results = await asyncio.gather(*[cache.get_or_set("k", slow_factory) for _ in range(10)])
    assert results == ["value"] * 10
    assert counter["n"] == 1


def test_make_key_stable_across_dict_ordering():
    assert make_key("p", {"b": 2, "a": 1}) == make_key("p", {"a": 1, "b": 2})


@pytest.mark.asyncio
async def test_cache_invalidate_removes_key():
    cache = AsyncTTLCache(maxsize=8, ttl=60.0)
    await cache.get_or_set("k", lambda: _coroutine("v1"))
    cache.invalidate("k")
    second = await cache.get_or_set("k", lambda: _coroutine("v2"))
    assert second == "v2"


async def _coroutine(v):
    return v
