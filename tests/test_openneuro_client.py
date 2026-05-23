"""OpenNeuro client tests using httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from neuro_research_discovery.clients.openneuro import OpenNeuroClient
from tests.conftest import patch_httpx_client


def _search_response(nodes: list[dict] | None, errors: list | None = None):
    payload = {
        "data": {
            "advancedSearch": {
                "edges": [{"cursor": "c", "node": n} for n in (nodes or [])],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }
    if errors:
        payload["errors"] = errors
    return payload


def _dataset_response(ds: dict):
    return {"data": {"dataset": ds}}


@pytest.mark.asyncio
async def test_search_returns_nodes():
    client = OpenNeuroClient()
    node = {
        "id": "ds000001",
        "publishDate": "2020-01-01",
        "latestSnapshot": {
            "tag": "1.0.0",
            "description": {"Name": "Test Dataset"},
            "summary": {"modalities": ["MRI"], "subjects": ["sub-01"], "tasks": ["rest"]},
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_response([node]))

    patch_httpx_client(client, handler)
    try:
        out = await client.search_datasets("autism", "mri", first=5)
        assert len(out) == 1
        assert out[0]["id"] == "ds000001"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_filters_null_nodes_when_errors_partial():
    """OpenNeuro leaks private datasets as null nodes + a top-level error.
    The client should keep the non-null nodes and not raise."""
    client = OpenNeuroClient()
    good = {"id": "ds000002", "latestSnapshot": {"tag": "1.0.0", "description": {"Name": "OK"},
                                                  "summary": {"modalities": ["MRI"], "subjects": [], "tasks": []}}}

    def handler(req: httpx.Request) -> httpx.Response:
        payload = _search_response([good, None], errors=[{"message": "You do not have access"}])
        # Re-encode after manually inserting None nodes.
        payload["data"]["advancedSearch"]["edges"] = [
            {"cursor": "1", "node": good}, {"cursor": "2", "node": None}
        ]
        return httpx.Response(200, json=payload)

    patch_httpx_client(client, handler)
    try:
        out = await client.search_datasets("anything", None, first=5)
        assert len(out) == 1 and out[0]["id"] == "ds000002"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_dataset_caches_repeat_calls():
    client = OpenNeuroClient()
    ds = {
        "id": "ds000001",
        "name": "Test",
        "metadata": {"species": "Human", "associatedPaperDOI": "10.1/x"},
        "latestSnapshot": {
            "tag": "1.0.0",
            "readme": "Hello world",
            "description": {"Name": "Test Dataset", "DatasetDOI": "10.18112/openneuro.ds000001"},
            "summary": {"modalities": ["MRI"], "subjects": ["sub-01", "sub-02"], "sessions": [], "tasks": ["rest"]},
        },
    }
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_dataset_response(ds))

    patch_httpx_client(client, handler)
    try:
        a = await client.get_dataset("ds000001")
        b = await client.get_dataset("ds000001")
        assert a == b
        assert calls["n"] == 1, "second call should hit cache"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_dataset_raises_when_data_null():
    client = OpenNeuroClient()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"dataset": None}})

    patch_httpx_client(client, handler)
    try:
        with pytest.raises(Exception):
            await client.get_dataset("ds-nonexistent")
    finally:
        await client.aclose()
