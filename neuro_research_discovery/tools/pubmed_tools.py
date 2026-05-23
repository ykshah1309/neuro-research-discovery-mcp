"""Family C — PubMed tools."""

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


def _article(record: dict) -> PubMedArticle:
    return PubMedArticle(**record)


async def search_pubmed(
    params: SearchPubMedInput, client: PubMedClient
) -> PubMedSearchResult:
    search = await client.esearch(
        term=params.query,
        retmax=params.max_results,
        date_range_years=params.date_range_years,
    )
    pmids = search["ids"]
    articles = [_article(r) for r in await client.efetch_articles(pmids)] if pmids else []
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
    return PubMedAbstract(pmid=r["pmid"], title=r["title"], abstract=r["abstract"])


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
