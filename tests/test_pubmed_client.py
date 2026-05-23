"""PubMed client tests using the FakeEntrez fixture."""

from __future__ import annotations

import pytest

from neuro_research_discovery.clients.pubmed import PubMedClient
from tests.conftest import FakeEntrez, make_elink_xml, make_esearch_xml, make_pubmed_efetch_xml


@pytest.mark.asyncio
async def test_esearch_returns_ids_and_count(fake_entrez: FakeEntrez):
    fake_entrez.esearch_response = make_esearch_xml(["111", "222"], count=42)
    client = PubMedClient()
    res = await client.esearch("default mode network", retmax=10)
    assert res == {"ids": ["111", "222"], "count": 42}
    assert fake_entrez.last_esearch_kwargs["term"] == "default mode network"


@pytest.mark.asyncio
async def test_efetch_parses_structured_abstract(fake_entrez: FakeEntrez):
    fake_entrez.efetch_response = make_pubmed_efetch_xml([
        {
            "pmid": "111",
            "title": "A study",
            "authors": [{"first": "Alice", "last": "Smith"}, {"first": "Bob", "last": "Jones"}],
            "journal": "Neuroimage",
            "year": "2024",
            "abstract_segments": ["Background: foo.", "Methods: bar.", "Results: baz."],
            "doi": "10.1/abc",
            "mesh": ["Brain", "fMRI"],
        }
    ])
    client = PubMedClient()
    arts = await client.efetch_articles(["111"])
    assert len(arts) == 1
    a = arts[0]
    assert a["pmid"] == "111"
    assert a["authors"] == ["Alice Smith", "Bob Jones"]
    assert "Background" in a["abstract"] and "Methods" in a["abstract"]
    assert a["doi"] == "10.1/abc"
    assert a["year"] == 2024
    assert "Brain" in a["mesh_terms"]


@pytest.mark.asyncio
async def test_doi_to_pmid_uses_esearch_term(fake_entrez: FakeEntrez):
    fake_entrez.esearch_response = make_esearch_xml(["999"])
    client = PubMedClient()
    pmid = await client.doi_to_pmid("10.1/abc")
    assert pmid == "999"
    # crucially: term must be the [DOI]-suffixed form, NOT elink
    assert fake_entrez.last_esearch_kwargs["term"] == "10.1/abc[DOI]"


@pytest.mark.asyncio
async def test_doi_to_pmid_returns_none_when_no_hits(fake_entrez: FakeEntrez):
    fake_entrez.esearch_response = make_esearch_xml([])
    client = PubMedClient()
    pmid = await client.doi_to_pmid("10.0/nonexistent")
    assert pmid is None


@pytest.mark.asyncio
async def test_elink_related_excludes_source_pmid(fake_entrez: FakeEntrez):
    fake_entrez.elink_response = make_elink_xml("100", ["200", "300", "400"])
    client = PubMedClient()
    out = await client.elink_related("100")
    assert "100" not in out
    assert out == ["200", "300", "400"]
