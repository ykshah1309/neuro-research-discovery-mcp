"""Tool-layer tests — verify the input/output Pydantic contract end-to-end
against mocked clients."""

from __future__ import annotations

import httpx
import pytest

from neuro_research_discovery.clients.neurovault import NeuroVaultClient
from neuro_research_discovery.clients.openneuro import OpenNeuroClient
from neuro_research_discovery.clients.pubmed import PubMedClient
from neuro_research_discovery.models import (
    FindNeuroVaultMapsForPaperInput,
    FindPapersUsingDatasetInput,
    GetOpenNeuroDatasetInput,
    SearchNeuroVaultCollectionsInput,
    SearchOpenNeuroInput,
)
from neuro_research_discovery.tools import bridge_tools, neurovault_tools, openneuro_tools
from tests.conftest import FakeEntrez, make_esearch_xml, make_pubmed_efetch_xml, patch_httpx_client


# ---- OpenNeuro tools ----

@pytest.mark.asyncio
async def test_get_openneuro_dataset_extracts_all_fields():
    client = OpenNeuroClient()
    payload = {
        "data": {
            "dataset": {
                "id": "ds000001",
                "name": "Raw",
                "metadata": {
                    "species": "Human",
                    "associatedPaperDOI": "10.1/paper",
                    "openneuroPaperDOI": "",
                    "studyDomain": "fMRI",
                },
                "latestSnapshot": {
                    "tag": "1.0.0",
                    "readme": "A demo dataset for testing.",
                    "description": {
                        "Name": "BIDS Demo",
                        "Authors": ["Jane Doe"],
                        "DatasetDOI": "10.18112/openneuro.ds000001",
                        "License": "CC0",
                        "ReferencesAndLinks": ["https://example.org"],
                    },
                    "summary": {
                        "modalities": ["MRI"],
                        "primaryModality": "MRI",
                        "subjects": ["sub-01", "sub-02"],
                        "sessions": [],
                        "tasks": ["rest"],
                        "totalFiles": 10,
                        "size": 1000,
                    },
                },
            }
        }
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    patch_httpx_client(client, handler)
    try:
        result = await openneuro_tools.get_openneuro_dataset(
            GetOpenNeuroDatasetInput(accession_number="ds000001"), client
        )
        assert result.accession_number == "ds000001"
        assert result.title == "BIDS Demo"
        assert result.num_subjects == 2
        assert result.species == "Human"
        assert result.tasks == ["rest"]
        # DatasetDOI plus associatedPaperDOI should both be picked up
        assert "10.18112/openneuro.ds000001" in result.associated_publications
        assert "10.1/paper" in result.associated_publications
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_openneuro_returns_summaries():
    client = OpenNeuroClient()
    nodes = [
        {
            "id": "ds00000X",
            "latestSnapshot": {
                "tag": "1.0.0",
                "description": {"Name": f"Dataset {i}"},
                "summary": {"modalities": ["MRI"], "subjects": ["sub-01"], "tasks": []},
            },
        }
        for i in range(3)
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "data": {
                "advancedSearch": {
                    "edges": [{"cursor": str(i), "node": n} for i, n in enumerate(nodes)],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        })

    patch_httpx_client(client, handler)
    try:
        result = await openneuro_tools.search_openneuro_datasets(
            SearchOpenNeuroInput(query="autism", modality="mri", max_results=10), client
        )
        assert result.total_returned == 3
        assert result.datasets[0].title == "Dataset 0"
    finally:
        await client.aclose()


# ---- NeuroVault tools ----

@pytest.mark.asyncio
async def test_search_neurovault_collections_filters_by_keyword():
    client = NeuroVaultClient()
    collections = [
        {"id": 1, "name": "Stroop task fMRI", "description": "", "DOI": None,
         "preprint_DOI": None, "authors": "", "journal_name": "", "paper_url": None,
         "number_of_images": 2, "download_url": None},
        {"id": 2, "name": "Working memory n-back", "description": "", "DOI": None,
         "preprint_DOI": None, "authors": "", "journal_name": "", "paper_url": None,
         "number_of_images": 5, "download_url": None},
        {"id": 3, "name": "Resting state", "description": "stroop comparison", "DOI": None,
         "preprint_DOI": None, "authors": "", "journal_name": "", "paper_url": None,
         "number_of_images": 1, "download_url": None},
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 3, "next": None, "previous": None, "results": collections})

    patch_httpx_client(client, handler)
    try:
        result = await neurovault_tools.search_neurovault_collections(
            SearchNeuroVaultCollectionsInput(query="stroop", max_results=10), client
        )
        ids = [c.collection_id for c in result.collections]
        assert 1 in ids and 3 in ids and 2 not in ids
    finally:
        await client.aclose()


# ---- Bridge tools ----

@pytest.mark.asyncio
async def test_find_neurovault_maps_for_paper_matches_doi(fake_entrez: FakeEntrez):
    fake_entrez.efetch_response = make_pubmed_efetch_xml([{
        "pmid": "5555",
        "title": "Paper",
        "authors": [{"first": "A", "last": "B"}],
        "journal": "J",
        "year": "2024",
        "abstract": "abs",
        "doi": "10.1/match",
        "mesh": [],
    }])
    pubmed = PubMedClient()
    nv = NeuroVaultClient()

    collections = [
        {"id": 7, "name": "Matching", "description": "", "DOI": "10.1/match",
         "preprint_DOI": None, "authors": "", "journal_name": "", "paper_url": None,
         "number_of_images": 3, "download_url": None},
        {"id": 8, "name": "Not matching", "description": "", "DOI": "10.2/other",
         "preprint_DOI": None, "authors": "", "journal_name": "", "paper_url": None,
         "number_of_images": 1, "download_url": None},
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 2, "next": None, "previous": None, "results": collections})

    patch_httpx_client(nv, handler)
    try:
        out = await bridge_tools.find_neurovault_maps_for_paper(
            FindNeuroVaultMapsForPaperInput(pmid="5555"), pubmed, nv
        )
        assert len(out.neurovault_collections) == 1
        assert out.neurovault_collections[0].collection_id == 7
        assert out.pubmed_articles[0].pmid == "5555"
    finally:
        await nv.aclose()


@pytest.mark.asyncio
async def test_find_neurovault_maps_for_paper_emits_doi_exact_evidence(fake_entrez: FakeEntrez):
    """Bridge tools must label *how* each result is linked to the query."""
    fake_entrez.efetch_response = make_pubmed_efetch_xml([{
        "pmid": "7777", "title": "P", "authors": [{"first": "X", "last": "Y"}],
        "journal": "J", "year": "2024", "abstract": "abs", "doi": "10.99/exact", "mesh": [],
    }])
    pubmed = PubMedClient()
    nv = NeuroVaultClient()

    def handler(req):
        return httpx.Response(200, json={"count": 1, "next": None, "previous": None, "results": [
            {"id": 99, "name": "X", "description": "", "DOI": "10.99/EXACT",
             "preprint_DOI": None, "authors": None, "journal_name": None, "paper_url": None,
             "number_of_images": 1, "download_url": None},
        ]})

    patch_httpx_client(nv, handler)
    try:
        result = await bridge_tools.find_neurovault_maps_for_paper(
            FindNeuroVaultMapsForPaperInput(pmid="7777"), pubmed, nv
        )
        assert result.linkage_evidence.get("pubmed:7777") == "doi_exact"
        assert result.linkage_evidence.get("neurovault_collection:99") == "doi_exact"
    finally:
        await nv.aclose()


@pytest.mark.asyncio
async def test_neurovault_search_surfaces_partial_index_flag():
    """When the upstream pagination fails on later pages we should flag partial."""
    client = NeuroVaultClient()
    # count says there are 1500 records (3 pages of 500) but page 2 + 3 will 500-error.
    page1_items = [{"id": i, "name": f"x{i}", "description": "", "DOI": None,
                    "preprint_DOI": None, "authors": None, "journal_name": None, "paper_url": None,
                    "number_of_images": 0, "download_url": None} for i in range(500)]

    def handler(req: httpx.Request) -> httpx.Response:
        offset = int(req.url.params.get("offset", 0))
        if offset == 0:
            return httpx.Response(200, json={"count": 1500, "next": None, "previous": None, "results": page1_items})
        return httpx.Response(500, json={"error": "kaboom"})

    patch_httpx_client(client, handler)
    try:
        from neuro_research_discovery.models import SearchNeuroVaultCollectionsInput as Q
        result = await neurovault_tools.search_neurovault_collections(
            Q(query="x42", max_results=5), client
        )
        assert result.index_partial is True
        assert result.index_note and "incomplete" in result.index_note
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_find_papers_using_dataset_chains_doi_lookup(fake_entrez: FakeEntrez):
    # esearch returns PMID 12345; efetch returns one article record.
    fake_entrez.esearch_response = make_esearch_xml(["12345"])
    fake_entrez.efetch_response = make_pubmed_efetch_xml([{
        "pmid": "12345",
        "title": "Source paper",
        "authors": [{"first": "X", "last": "Y"}],
        "journal": "Cool J",
        "year": "2023",
        "abstract": "abstract here",
        "doi": "10.1/paper",
        "mesh": [],
    }])

    openneuro = OpenNeuroClient()
    pubmed = PubMedClient()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"dataset": {
            "id": "ds000099",
            "name": "X",
            "metadata": {"species": "Human", "associatedPaperDOI": "10.1/paper"},
            "latestSnapshot": {
                "tag": "1.0.0", "readme": "",
                "description": {"Name": "X", "DatasetDOI": "10.18112/openneuro.ds000099"},
                "summary": {"modalities": ["MRI"], "subjects": [], "sessions": [], "tasks": []},
            },
        }}})

    patch_httpx_client(openneuro, handler)
    try:
        result = await bridge_tools.find_papers_using_dataset(
            FindPapersUsingDatasetInput(openneuro_accession="ds000099"), openneuro, pubmed
        )
        assert len(result.pubmed_articles) == 1
        assert result.pubmed_articles[0].pmid == "12345"
        assert result.openneuro_datasets[0].accession_number == "ds000099"
    finally:
        await openneuro.aclose()
