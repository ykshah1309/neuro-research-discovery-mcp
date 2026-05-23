"""Tests for the v0.3.1 cache admin tools: get_neurovault_cache_status, prewarm_neurovault_index."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from neuro_research_discovery import disk_cache
from neuro_research_discovery.clients.neurovault import NeuroVaultClient
from neuro_research_discovery.models import (
    NeuroVaultCacheStatusInput,
    PrewarmNeuroVaultIndexInput,
)
from neuro_research_discovery.tools import neurovault_tools
from tests.conftest import patch_httpx_client


@pytest.fixture
def _tmp_cache(monkeypatch, tmp_path: Path):
    """Point all disk_cache module-level paths at a tmp dir."""
    monkeypatch.setattr(disk_cache, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(disk_cache, "_index_path", lambda: tmp_path / "neurovault_index.json")
    # The autouse fixture in conftest.py monkeypatches the symbol that
    # neurovault.py imported at module level. We want the cache-status tool
    # to see the real load function, so re-bind it here only for cache-admin tests.
    from neuro_research_discovery.tools import neurovault_tools as nv_tools_mod
    monkeypatch.setattr(nv_tools_mod, "load_neurovault_index", disk_cache.load_neurovault_index)
    monkeypatch.setattr(nv_tools_mod, "_index_path", disk_cache._index_path)
    return tmp_path


@pytest.mark.asyncio
async def test_cache_status_reports_missing_when_nothing_cached(_tmp_cache):
    client = NeuroVaultClient()
    status = await neurovault_tools.get_neurovault_cache_status(
        NeuroVaultCacheStatusInput(), client
    )
    assert status.status == "missing"
    assert status.in_memory_loaded is False
    assert status.on_disk_present is False
    assert "cold build" in status.notes.lower()


@pytest.mark.asyncio
async def test_cache_status_reports_fresh_after_write(_tmp_cache):
    projections = [{"id": i, "name": f"x{i}"} for i in range(10)]
    disk_cache.save_neurovault_index(projections, ttl_seconds=3600, partial=False)
    client = NeuroVaultClient()
    status = await neurovault_tools.get_neurovault_cache_status(
        NeuroVaultCacheStatusInput(), client
    )
    assert status.status == "fresh"
    assert status.on_disk_present is True
    assert status.collection_count == 10
    assert status.schema_version == disk_cache.NEUROVAULT_INDEX_SCHEMA_VERSION
    assert status.size_bytes is not None and status.size_bytes > 0
    assert status.age_seconds is not None and status.age_seconds < 10


@pytest.mark.asyncio
async def test_cache_status_detects_stale_but_serveable(_tmp_cache):
    """An entry past TTL but inside 2x TTL is stale_but_serveable."""
    # built_at 5000s ago, ttl 3600 → past TTL but well within 2x
    payload = {
        "schema_version": disk_cache.NEUROVAULT_INDEX_SCHEMA_VERSION,
        "built_at": time.time() - 5000,
        "ttl": 3600,
        "partial": False,
        "projections": [{"id": 1, "name": "x"}],
    }
    (_tmp_cache / "neurovault_index.json").write_text(json.dumps(payload), encoding="utf-8")
    client = NeuroVaultClient()
    status = await neurovault_tools.get_neurovault_cache_status(
        NeuroVaultCacheStatusInput(), client
    )
    assert status.status == "stale_but_serveable"


@pytest.mark.asyncio
async def test_prewarm_skipped_when_fresh(_tmp_cache):
    projections = [{"id": i, "name": f"x{i}"} for i in range(5)]
    disk_cache.save_neurovault_index(projections, ttl_seconds=3600, partial=False)
    client = NeuroVaultClient()
    report = await neurovault_tools.prewarm_neurovault_index(
        PrewarmNeuroVaultIndexInput(force_refresh=False), client
    )
    assert report.action == "already_fresh_skipped"
    assert report.elapsed_seconds == 0.0
    assert report.collection_count == 5


@pytest.mark.asyncio
async def test_prewarm_force_refresh_triggers_rebuild(_tmp_cache, monkeypatch):
    # Patch the client to simulate a single-page upstream so the rebuild is fast.
    client = NeuroVaultClient()
    page = {"count": 2, "next": None, "previous": None, "results": [
        {"id": 1, "name": "a"}, {"id": 2, "name": "b"},
    ]}

    def handler(req):
        return httpx.Response(200, json=page)

    patch_httpx_client(client, handler)
    # Re-allow disk writes for this test (autouse fixture in conftest disables them).
    from neuro_research_discovery.clients import neurovault as nv_module
    monkeypatch.setattr(nv_module, "load_neurovault_index", disk_cache.load_neurovault_index)
    monkeypatch.setattr(nv_module, "save_neurovault_index", disk_cache.save_neurovault_index)
    try:
        report = await neurovault_tools.prewarm_neurovault_index(
            PrewarmNeuroVaultIndexInput(force_refresh=True), client
        )
        assert report.action == "rebuilt"
        assert report.collection_count == 2
    finally:
        await client.aclose()
