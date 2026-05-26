"""NeuroVault client tests."""

from __future__ import annotations

import httpx
import pytest

from neuro_research_discovery.clients.neurovault import NeuroVaultClient
from tests.conftest import patch_httpx_client


def _page(results, count, next_url=None):
    return {"count": count, "next": next_url, "previous": None, "results": results}


def _coll(cid: int, name: str = "X", doi: str | None = None):
    return {
        "id": cid,
        "name": name,
        "description": "",
        "DOI": doi,
        "preprint_DOI": None,
        "authors": "Author A",
        "journal_name": "J",
        "paper_url": None,
        "number_of_images": 1,
        "download_url": f"https://neurovault.org/collections/{cid}/download",
    }


@pytest.mark.asyncio
async def test_index_paginates_until_exhausted():
    client = NeuroVaultClient()
    # 750 collections -> 2 pages at 500/page
    page1 = [_coll(i) for i in range(500)]
    page2 = [_coll(i) for i in range(500, 750)]
    page_calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        page_calls["n"] += 1
        offset = int(req.url.params.get("offset", 0))
        if offset == 0:
            # Build absolute next URL so client follows it.
            base = str(req.url).split("?")[0]
            return httpx.Response(200, json=_page(page1, 750, next_url=f"{base}?limit=500&offset=500"))
        return httpx.Response(200, json=_page(page2, 750, next_url=None))

    patch_httpx_client(client, handler)
    try:
        idx = await client.get_index(force_refresh=True)
        assert len(idx) == 750
        assert idx[0]["id"] == 0 and idx[-1]["id"] == 749
        assert page_calls["n"] == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_in_process_age_uses_monotonic_after_disk_load(monkeypatch):
    """Regression test for the v0.4.0-web disk-cache TTL bug.

    The in-process age check used `time.monotonic() - self._index_built_at`,
    but `_index_built_at` was being set from the disk entry's wall-clock
    `built_at` field. The unit mismatch produced a huge negative "age" that
    always passed the TTL check, so a process that loaded a stale disk
    cache would serve it forever, never refreshing.

    Fix: only store `time.monotonic()` in `_index_built_at`. The disk
    entry's wall-clock `built_at` is consulted via `is_fresh`/`is_serveable`,
    never copied into the monotonic field.
    """
    import time
    from neuro_research_discovery.clients import neurovault as nv_module

    # Pretend disk has a fresh entry built 30s ago in wall-clock terms.
    fake_entry = {
        "schema_version": 2,
        "built_at": time.time() - 30,
        "ttl": 3600,
        "partial": False,
        "projections": [{"id": 1, "name": "A"}],
    }
    monkeypatch.setattr(nv_module, "load_neurovault_index", lambda: fake_entry)

    client = NeuroVaultClient()
    await client.get_index()
    age = client._index_age()
    # If the bug returns, age will be a large negative number (wall-clock
    # subtracted from a small monotonic counter).
    assert age >= 0, (
        f"_index_age() returned {age}; this means _index_built_at was set "
        "from wall-clock time, not time.monotonic(). The disk-cache TTL "
        "regression is back."
    )
    # And the age should be small (we just loaded it).
    assert age < 60, f"_index_age() = {age}s; expected near-zero right after load"


@pytest.mark.asyncio
async def test_get_collection_caches():
    client = NeuroVaultClient()
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_coll(42, name="Stroop", doi="10.1/foo"))

    patch_httpx_client(client, handler)
    try:
        a = await client.get_collection(42)
        b = await client.get_collection(42)
        assert a["id"] == b["id"] == 42
        assert calls["n"] == 1
    finally:
        await client.aclose()
