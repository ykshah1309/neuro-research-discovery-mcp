"""Live integration tests — opt-in via `pytest -m integration`.

These hit real upstreams (OpenNeuro, NeuroVault, PubMed). Skip by default in CI
because they're slow and network-dependent. Run them locally before cutting a
release.

Latency budgets are intentionally generous — the upstreams can spike.
"""

from __future__ import annotations

import os
import time

import pytest

from neuro_research_discovery.clients.openneuro import OpenNeuroClient
from neuro_research_discovery.clients.pubmed import PubMedClient
from neuro_research_discovery.models import (
    GetOpenNeuroDatasetInput,
    SearchOpenNeuroInput,
    SearchPubMedInput,
)
from neuro_research_discovery.tools.openneuro_tools import (
    get_openneuro_dataset,
    search_openneuro_datasets,
)
from neuro_research_discovery.tools.pubmed_tools import search_pubmed

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pubmed_search_returns_articles_with_abstracts():
    client = PubMedClient()
    t0 = time.monotonic()
    result = await search_pubmed(
        SearchPubMedInput(query="default mode network autism", max_results=2),
        client,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"PubMed search took {elapsed:.1f}s (>10s budget)"
    assert result.returned >= 1
    assert result.articles[0].pmid
    assert result.articles[0].title


@pytest.mark.asyncio
async def test_openneuro_search_with_modality():
    client = OpenNeuroClient()
    try:
        t0 = time.monotonic()
        result = await search_openneuro_datasets(
            SearchOpenNeuroInput(query="autism", modality="mri", max_results=3),
            client,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 10.0, f"OpenNeuro search took {elapsed:.1f}s"
        assert result.total_returned >= 1
        # Acceptance criterion: at least one returned dataset has a usable accession
        assert all(d.accession_number.startswith("ds") for d in result.datasets)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_openneuro_get_dataset_includes_doi():
    client = OpenNeuroClient()
    try:
        result = await get_openneuro_dataset(
            GetOpenNeuroDatasetInput(accession_number="ds000001"), client
        )
        assert result.accession_number == "ds000001"
        # ds000001 has a DatasetDOI; should be in associated_publications
        assert any("10." in p for p in result.associated_publications)
    finally:
        await client.aclose()


@pytest.mark.skipif(
    os.environ.get("RUN_SLOW_TESTS") != "1",
    reason="NeuroVault cold index build is ~60s; set RUN_SLOW_TESTS=1 to opt in",
)
@pytest.mark.asyncio
async def test_neurovault_index_cold_build_completes():
    from neuro_research_discovery.clients.neurovault import NeuroVaultClient
    client = NeuroVaultClient()
    try:
        t0 = time.monotonic()
        idx = await client.get_index(force_refresh=True)
        elapsed = time.monotonic() - t0
        assert len(idx) > 1000, f"index too small: {len(idx)}"
        assert elapsed < 300.0, f"NeuroVault cold build took {elapsed:.1f}s (>300s budget)"
    finally:
        await client.aclose()
