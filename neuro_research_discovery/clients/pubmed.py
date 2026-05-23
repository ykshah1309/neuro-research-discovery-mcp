"""PubMed client wrapping biopython's Bio.Entrez.

Entrez is blocking urllib. Every public method here wraps the work in
asyncio.to_thread and goes through a per-process token-bucket so we stay
under NCBI's 3/sec (anonymous) or 10/sec (with API key) ceiling.

Field extraction lives in this module too — PubMed XML is famously nested
(StringElement, structured AbstractText lists, DOI in two places). Centralizing
the parsing here keeps the tool layer clean.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from Bio import Entrez

from .. import settings
from ..cache import AsyncTTLCache
from ..rate_limit import AsyncTokenBucket

# NCBI requires email + tool identification on every request.
Entrez.email = settings.PUBMED_EMAIL
Entrez.tool = settings.PUBMED_TOOL
if settings.PUBMED_API_KEY:
    Entrez.api_key = settings.PUBMED_API_KEY


class PubMedError(RuntimeError):
    pass


class PubMedClient:
    def __init__(self) -> None:
        self._bucket = AsyncTokenBucket(rate_per_sec=settings.PUBMED_RATE_PER_SEC)
        self._cache = AsyncTTLCache(maxsize=512, ttl=settings.DEFAULT_CACHE_TTL)

    async def aclose(self) -> None:  # parity with the other clients
        return None

    # ---- search ----

    async def esearch(
        self, term: str, retmax: int, date_range_years: int | None = None
    ) -> dict[str, Any]:
        key = f"esearch::{term}::{retmax}::{date_range_years}"
        return await self._cache.get_or_set(
            key, lambda: self._esearch_uncached(term, retmax, date_range_years)
        )

    async def _esearch_uncached(
        self, term: str, retmax: int, date_range_years: int | None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"db": "pubmed", "term": term, "retmax": retmax}
        if date_range_years is not None:
            current = datetime.utcnow().year
            kwargs["mindate"] = str(current - date_range_years)
            kwargs["maxdate"] = str(current)
            kwargs["datetype"] = "pdat"

        def call() -> dict[str, Any]:
            handle = Entrez.esearch(**kwargs)
            try:
                rec = Entrez.read(handle)
            finally:
                handle.close()
            return {
                "ids": list(rec.get("IdList") or []),
                "count": int(rec.get("Count") or 0),
            }

        await self._bucket.acquire()
        return await asyncio.to_thread(call)

    async def doi_to_pmid(self, doi: str) -> str | None:
        result = await self.esearch(term=f"{doi}[DOI]", retmax=1)
        ids = result.get("ids") or []
        return ids[0] if ids else None

    # ---- fetch ----

    async def efetch_articles(self, pmids: list[str]) -> list[dict[str, Any]]:
        """Batch-fetch articles. Returns one parsed dict per PMID."""
        if not pmids:
            return []
        unique = list(dict.fromkeys(pmids))
        # Cache key on the sorted comma-join so reorderings hit the same entry.
        key = "efetch::" + ",".join(sorted(unique))
        return await self._cache.get_or_set(
            key, lambda: self._efetch_uncached(unique)
        )

    async def _efetch_uncached(self, pmids: list[str]) -> list[dict[str, Any]]:
        def call() -> list[dict[str, Any]]:
            handle = Entrez.efetch(
                db="pubmed", id=",".join(pmids), rettype="medline", retmode="xml"
            )
            try:
                rec = Entrez.read(handle)
            finally:
                handle.close()
            return [_parse_article(art) for art in (rec.get("PubmedArticle") or [])]

        await self._bucket.acquire()
        return await asyncio.to_thread(call)

    # ---- related ----

    async def elink_related(self, pmid: str) -> list[str]:
        key = f"elink::{pmid}"
        return await self._cache.get_or_set(key, lambda: self._elink_uncached(pmid))

    async def _elink_uncached(self, pmid: str) -> list[str]:
        def call() -> list[str]:
            handle = Entrez.elink(
                dbfrom="pubmed", db="pubmed", id=pmid, linkname="pubmed_pubmed"
            )
            try:
                rec = Entrez.read(handle)
            finally:
                handle.close()
            if not rec:
                return []
            link_sets = rec[0].get("LinkSetDb") or []
            if not link_sets:
                return []
            links = link_sets[0].get("Link") or []
            # First entry is the query itself.
            out = [str(l["Id"]) for l in links]
            return [pid for pid in out if pid != str(pmid)]

        await self._bucket.acquire()
        return await asyncio.to_thread(call)


# --------------------------------------------------------------------------- #
# XML field extraction
# --------------------------------------------------------------------------- #

def _str(node: Any) -> str:
    return "" if node is None else str(node)


def _parse_article(art: dict[str, Any]) -> dict[str, Any]:
    mc = art.get("MedlineCitation") or {}
    a = mc.get("Article") or {}
    pmid = _str(mc.get("PMID"))

    title = _str(a.get("ArticleTitle"))
    journal = _str((a.get("Journal") or {}).get("Title"))

    pubdate = ((a.get("Journal") or {}).get("JournalIssue") or {}).get("PubDate") or {}
    year_raw = pubdate.get("Year") or pubdate.get("MedlineDate", "")[:4]
    try:
        year = int(year_raw) if year_raw else None
    except (TypeError, ValueError):
        year = None

    abstract_node = a.get("Abstract") or {}
    abstract_segments = abstract_node.get("AbstractText") or []
    if not isinstance(abstract_segments, list):
        abstract_segments = [abstract_segments]
    abstract = "\n".join(_str(seg) for seg in abstract_segments).strip()

    authors: list[str] = []
    for au in (a.get("AuthorList") or []):
        last = _str(au.get("LastName"))
        first = _str(au.get("ForeName"))
        if last and first:
            authors.append(f"{first} {last}")
        elif last:
            authors.append(last)
        elif au.get("CollectiveName"):
            authors.append(_str(au.get("CollectiveName")))

    mesh = [
        _str((m.get("DescriptorName")))
        for m in (mc.get("MeshHeadingList") or [])
    ]

    keyword_list_raw = mc.get("KeywordList") or []
    keywords: list[str] = []
    for group in keyword_list_raw:
        for kw in group:
            keywords.append(_str(kw))

    doi: str | None = None
    for e in (a.get("ELocationID") or []):
        if getattr(e, "attributes", {}).get("EIdType") == "doi":
            doi = _str(e)
            break
    if not doi:
        for aid in ((art.get("PubmedData") or {}).get("ArticleIdList") or []):
            if getattr(aid, "attributes", {}).get("IdType") == "doi":
                doi = _str(aid)
                break

    return {
        "pmid": pmid,
        "title": title,
        "authors": authors,
        "journal": journal,
        "year": year,
        "abstract": abstract,
        "doi": doi,
        "keywords": keywords,
        "mesh_terms": mesh,
    }
