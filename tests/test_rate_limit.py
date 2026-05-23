"""Tests for the asyncio token bucket."""

from __future__ import annotations

import asyncio
import time

import pytest

from neuro_research_discovery.rate_limit import AsyncTokenBucket


@pytest.mark.asyncio
async def test_burst_capacity_allows_immediate_acquires():
    bucket = AsyncTokenBucket(rate_per_sec=2.0, burst=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    # 5 within burst should be near-instant
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_extra_acquires_wait_for_refill():
    bucket = AsyncTokenBucket(rate_per_sec=10.0, burst=2)
    # Drain burst.
    await bucket.acquire()
    await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()  # must wait ~1/10 = 0.1s for a token
    waited = time.monotonic() - start
    assert waited >= 0.08, f"expected ~0.1s wait, got {waited:.3f}"
