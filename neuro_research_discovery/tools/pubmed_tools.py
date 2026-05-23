"""Family C — PubMed tools.

All upstream text (title, abstract, authors) is truncated to bound response
size — see UPGRADE_PLAN.md Tier 1b.
"""

from __future__ import annotations

from ..clients.pubmed import PubMedClient
from ..models import (
    FindRelatedPubMedInput,
    GetPubMedAbstractInput,
    GetPubMedArticleInput,
    PubMedAbstract,
    PubMedArticle,
    PubMedRelatedResult,
    PubMedSearchResult,
    SearchPubMedInput,
)
from ..doi import normalize_doi
from ..text_safety import make_untrusted, truncate_title


def _article(record: dict, include_abstract: bool = True) -> PubMedArticle:
    abstract_text = record.get("abstract") or "" if include_abstract else ""
    return PubMedArticle(
        pmid=record["pmid"],
        title=truncate_title(record.get("title") or ""),
        authors=record.get("authors") or [],
        journal=record.get("journal") or "",
        year=record.get("year"),
        abstract=make_untrusted(abstract_text, source="pubmed"),
        doi=normalize_doi(record.get("doi")),
        keywords=record.get("keywords") or [],
        mesh_terms=record.get("mesh_terms") or [],
    )


async def search_pubmed(
    params: SearchPubMedInput, client: PubMedClient
) -> PubMedSearchResult:
    search = await client.esearch(
        term=params.query,
        retmax=params.max_results,
        date_range_years=params.date_range_years,
    )
    pmids = search["ids"]
    raw = await client.efetch_articles(pmids) if pmids else []
    articles = [_article(r, include_abstract=params.include_abstracts) for r in raw]
    return PubMedSearchResult(
        query=params.query,
        total_hits=search["count"],
        returned=len(articles),
        pmids=pmids,
        articles=articles,
    )


async def get_pubmed_article(
    params: GetPubMedArticleInput, client: PubMedClient
) -> PubMedArticle:
    records = await client.efetch_articles([params.pmid])
    if not records:
        raise ValueError(f"PMID not found: {params.pmid}")
    return _article(records[0])


async def get_pubmed_article_abstract(
    params: GetPubMedAbstractInput, client: PubMedClient
) -> PubMedAbstract:
    records = await client.efetch_articles([params.pmid])
    if not records:
        raise ValueError(f"PMID not found: {params.pmid}")
    r = records[0]
    return PubMedAbstract(
        pmid=r["pmid"],
        title=truncate_title(r.get("title") or ""),
        abstract=make_untrusted(r.get("abstract") or "", source="pubmed"),
    )


async def find_related_pubmed_articles(
    params: FindRelatedPubMedInput, client: PubMedClient
) -> PubMedRelatedResult:
    related = await client.elink_related(params.pmid)
    capped = related[: params.max_results]
    articles = [_article(r) for r in await client.efetch_articles(capped)] if capped else []
    return PubMedRelatedResult(
        source_pmid=params.pmid,
        related_pmids=capped,
        articles=articles,
    )
