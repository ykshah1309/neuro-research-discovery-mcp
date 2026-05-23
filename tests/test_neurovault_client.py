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
