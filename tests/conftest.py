"""Shared pytest fixtures.

We use httpx.MockTransport for OpenNeuro and NeuroVault and monkeypatch
Bio.Entrez functions for PubMed.
"""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def patch_httpx_client(client_instance: Any, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Replace the AsyncClient on a client with one backed by a MockTransport."""
    asyncio.get_event_loop()  # ensure a loop exists for the new client
    new_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # Close the old one to free sockets (synchronously since we haven't opened any).
    old = client_instance._http
    client_instance._http = new_http
    # Schedule old closure on next event loop iteration if it ever ran.
    try:
        asyncio.get_event_loop().create_task(old.aclose())
    except RuntimeError:
        pass


# ---- biopython Entrez monkey-patch helpers ----

def _str_handle(text: str) -> Any:
    """Return a file-like object that behaves like an Entrez handle."""
    bio = BytesIO(text.encode("utf-8"))
    return bio


_EFETCH_DOCTYPE = (
    '<?xml version="1.0" ?>\n'
    '<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" '
    '"https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">'
)
_ESEARCH_DOCTYPE = (
    '<?xml version="1.0" encoding="UTF-8" ?>\n'
    '<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch 20060628//EN" '
    '"https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/esearch.dtd">'
)
_ELINK_DOCTYPE = (
    '<?xml version="1.0" encoding="UTF-8" ?>\n'
    '<!DOCTYPE eLinkResult PUBLIC "-//NLM//DTD elink 20101123//EN" '
    '"https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20101123/elink.dtd">'
)


def make_pubmed_efetch_xml(records: list[dict[str, Any]]) -> str:
    """Build a minimal PubmedArticleSet XML for testing."""
    parts = [_EFETCH_DOCTYPE, "<PubmedArticleSet>"]
    for r in records:
        authors_xml = "".join(
            f"<Author><LastName>{escape(a['last'])}</LastName>"
            f"<ForeName>{escape(a['first'])}</ForeName></Author>"
            for a in r.get("authors", [])
        )
        mesh_xml = "".join(
            f"<MeshHeading><DescriptorName>{escape(m)}</DescriptorName></MeshHeading>"
            for m in r.get("mesh", [])
        )
        # AbstractText can be a list of segments
        abs_segs = r.get("abstract_segments") or ([r["abstract"]] if r.get("abstract") else [])
        abstract_xml = "".join(f"<AbstractText>{escape(seg)}</AbstractText>" for seg in abs_segs)
        doi_xml = ""
        if r.get("doi"):
            doi_xml = f'<ELocationID EIdType="doi">{escape(r["doi"])}</ELocationID>'
        parts.append(
            f"""<PubmedArticle>
  <MedlineCitation>
    <PMID>{escape(str(r['pmid']))}</PMID>
    <Article>
      <Journal><Title>{escape(r.get('journal',''))}</Title>
        <JournalIssue><PubDate><Year>{escape(str(r.get('year','')))}</Year></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>{escape(r.get('title',''))}</ArticleTitle>
      <Abstract>{abstract_xml}</Abstract>
      <AuthorList>{authors_xml}</AuthorList>
      {doi_xml}
    </Article>
    <MeshHeadingList>{mesh_xml}</MeshHeadingList>
  </MedlineCitation>
  <PubmedData><ArticleIdList></ArticleIdList></PubmedData>
</PubmedArticle>"""
        )
    parts.append("</PubmedArticleSet>")
    return "\n".join(parts)


def make_esearch_xml(ids: list[str], count: int | None = None) -> str:
    count = count if count is not None else len(ids)
    id_xml = "".join(f"<Id>{i}</Id>" for i in ids)
    return f"""{_ESEARCH_DOCTYPE}
<eSearchResult>
  <Count>{count}</Count>
  <RetMax>{len(ids)}</RetMax>
  <RetStart>0</RetStart>
  <IdList>{id_xml}</IdList>
</eSearchResult>"""


def make_elink_xml(source_pmid: str, linked_pmids: list[str]) -> str:
    links_xml = "".join(
        f"<Link><Id>{pid}</Id></Link>" for pid in [source_pmid, *linked_pmids]
    )
    return f"""{_ELINK_DOCTYPE}
<eLinkResult>
  <LinkSet>
    <DbFrom>pubmed</DbFrom>
    <IdList><Id>{source_pmid}</Id></IdList>
    <LinkSetDb>
      <DbTo>pubmed</DbTo>
      <LinkName>pubmed_pubmed</LinkName>
      {links_xml}
    </LinkSetDb>
  </LinkSet>
</eLinkResult>"""


class FakeEntrez:
    """Drop-in for Bio.Entrez. The PubMedClient calls esearch/efetch/elink."""

    def __init__(self) -> None:
        self.esearch_response: str = make_esearch_xml([])
        self.efetch_response: str = make_pubmed_efetch_xml([])
        self.elink_response: str = make_elink_xml("0", [])
        self.last_esearch_kwargs: dict[str, Any] = {}
        self.last_efetch_kwargs: dict[str, Any] = {}
        self.last_elink_kwargs: dict[str, Any] = {}

    def esearch(self, **kwargs: Any) -> Any:
        self.last_esearch_kwargs = kwargs
        return _str_handle(self.esearch_response)

    def efetch(self, **kwargs: Any) -> Any:
        self.last_efetch_kwargs = kwargs
        return _str_handle(self.efetch_response)

    def elink(self, **kwargs: Any) -> Any:
        self.last_elink_kwargs = kwargs
        return _str_handle(self.elink_response)


@pytest.fixture
def fake_entrez(monkeypatch: pytest.MonkeyPatch) -> FakeEntrez:
    from Bio import Entrez

    fake = FakeEntrez()
    monkeypatch.setattr(Entrez, "esearch", fake.esearch)
    monkeypatch.setattr(Entrez, "efetch", fake.efetch)
    monkeypatch.setattr(Entrez, "elink", fake.elink)
    return fake
