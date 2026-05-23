"""Family D — Bridge tools.

These tools chain calls across the three clients. They are the actual value-add
of this MCP — agents get a single tool that resolves a question that would
otherwise take three separate searches and a manual DOI cross-walk.

`linkage_evidence` on every CrossSourceResult tells the agent *how* each result
was linked to the query:
- "doi_exact"   — matched by exact DOI cross-reference (strongest)
- "doi_metadata"— matched via a metadata DOI field (close to exact)
- "keyword_match" — matched only by keyword similarity (weakest)

Fan-out is bounded with asyncio.Semaphore. The omnibus search runs an extra
DOI-resolution pass against NeuroVault for the top PubMed papers — this is
what makes the bridge tool genuinely cross-source rather than three parallel
keyword searches.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..clients.neurovault import NeuroVaultClient
from ..clients.openneuro import OpenNeuroClient
from ..clients.pubmed import PubMedClient
from ..models import (
    ComprehensiveLiteratureSearchInput,
    CrossSourceResult,
    FindDatasetsForTopicInput,
    FindNeuroVaultMapsForPaperInput,
    FindPapersUsingDatasetInput,
    NeuroVaultCollection,
    PubMedArticle,
    SearchNeuroVaultCollectionsInput,
    SearchOpenNeuroInput,
    SearchPubMedInput,
)
from .neurovault_tools import _collection_from_projection, search_neurovault_collections
from .openneuro_tools import _collect_dois, _summary, search_openneuro_datasets
from .pubmed_tools import search_pubmed

LITERATURE_TOP_PAPERS = 5
LITERATURE_MESH_TOP_TERMS = 5
DOI_LOOKUP_CONCURRENCY = 4


def _nv_key(cid: int) -> str:
    return f"neurovault_collection:{cid}"


def _on_key(accession: str) -> str:
    return f"openneuro_dataset:{accession}"


def _pm_key(pmid: str) -> str:
    return f"pubmed:{pmid}"


async def find_papers_using_dataset(
    params: FindPapersUsingDatasetInput,
    openneuro: OpenNeuroClient,
    pubmed: PubMedClient,
) -> CrossSourceResult:
    ds = await openneuro.get_dataset(params.openneuro_accession)
    dois = _collect_dois(ds)
    summary = _summary({"id": ds.get("id"), "latestSnapshot": ds.get("latestSnapshot")})

    if not dois:
        return CrossSourceResult(
            query=params.openneuro_accession,
            openneuro_datasets=[summary],
            linkage_evidence={_on_key(summary.accession_number): "doi_exact"},
            notes="No DOIs are associated with this dataset on OpenNeuro, so no PubMed lookup was possible.",
        )

    sem = asyncio.Semaphore(DOI_LOOKUP_CONCURRENCY)

    async def resolve(doi: str) -> str | None:
        async with sem:
            return await pubmed.doi_to_pmid(doi)

    pmids = [p for p in await asyncio.gather(*[resolve(d) for d in dois]) if p]
    articles_raw = await pubmed.efetch_articles(pmids) if pmids else []
    articles = [PubMedArticle(**r) for r in articles_raw]

    evidence: dict[str, str] = {_on_key(summary.accession_number): "doi_exact"}
    for a in articles:
        evidence[_pm_key(a.pmid)] = "doi_exact"

    return CrossSourceResult(
        query=params.openneuro_accession,
        openneuro_datasets=[summary],
        pubmed_articles=articles,
        linkage_evidence=evidence,
        notes=(
            f"Resolved {len(pmids)}/{len(dois)} dataset DOI(s) to PubMed records. "
            "DOIs that don't match a PubMed record are typically preprints or non-indexed venues."
        ),
    )


async def find_neurovault_maps_for_paper(
    params: FindNeuroVaultMapsForPaperInput,
    pubmed: PubMedClient,
    neurovault: NeuroVaultClient,
) -> CrossSourceResult:
    records = await pubmed.efetch_articles([params.pmid])
    if not records:
        return CrossSourceResult(
            query=params.pmid,
            notes=f"PubMed has no record for PMID {params.pmid}.",
        )
    article = PubMedArticle(**records[0])
    if not article.doi:
        return CrossSourceResult(
            query=params.pmid,
            pubmed_articles=[article],
            linkage_evidence={_pm_key(article.pmid): "doi_exact"},
            notes="PubMed record has no DOI — cannot look up associated NeuroVault collections.",
        )

    index = await neurovault.get_index()
    matches = _doi_match_collections(index, article.doi)
    collections: list[NeuroVaultCollection] = [_collection_from_projection(p) for p in matches]

    evidence: dict[str, str] = {_pm_key(article.pmid): "doi_exact"}
    for c in collections:
        evidence[_nv_key(c.collection_id)] = "doi_exact"

    return CrossSourceResult(
        query=params.pmid,
        pubmed_articles=[article],
        neurovault_collections=collections,
        linkage_evidence=evidence,
        notes=(
            f"Matched {len(collections)} NeuroVault collection(s) on DOI {article.doi}. "
            "NeuroVault metadata is uploader-supplied, so absence here doesn't prove no maps exist."
        ),
    )


async def find_datasets_for_topic(
    params: FindDatasetsForTopicInput,
    openneuro: OpenNeuroClient,
    neurovault: NeuroVaultClient,
) -> CrossSourceResult:
    on_input = SearchOpenNeuroInput(query=params.research_topic, modality=params.modality, max_results=10)
    nv_input = SearchNeuroVaultCollectionsInput(query=params.research_topic, max_results=10)

    on_result, nv_result = await asyncio.gather(
        search_openneuro_datasets(on_input, openneuro),
        search_neurovault_collections(nv_input, neurovault),
    )

    evidence: dict[str, str] = {}
    for d in on_result.datasets:
        evidence[_on_key(d.accession_number)] = "keyword_match"
    for c in nv_result.collections:
        evidence[_nv_key(c.collection_id)] = "keyword_match"

    return CrossSourceResult(
        query=params.research_topic,
        openneuro_datasets=on_result.datasets,
        neurovault_collections=nv_result.collections,
        linkage_evidence=evidence,
        notes=(
            f"OpenNeuro returned {on_result.total_returned} dataset(s); "
            f"NeuroVault returned {nv_result.total_returned} collection(s). "
            "All matches above are keyword-only — none are confirmed linked to specific papers. "
            "For DOI-confirmed links, call find_papers_using_dataset or find_neurovault_maps_for_paper."
        ),
    )


async def comprehensive_literature_search(
    params: ComprehensiveLiteratureSearchInput,
    pubmed: PubMedClient,
    openneuro: OpenNeuroClient,
    neurovault: NeuroVaultClient,
) -> CrossSourceResult:
    """Omnibus search: PubMed → MeSH extraction → topic-search OpenNeuro &
    NeuroVault → DOI-link top papers to NeuroVault collections.

    The DOI linking step is what makes this genuinely cross-source. Without
    it the result would just be three parallel keyword searches.
    """
    # 1) PubMed search (with abstracts)
    pm_result = await search_pubmed(
        SearchPubMedInput(query=params.research_question, max_results=LITERATURE_TOP_PAPERS),
        pubmed,
    )

    # 2) MeSH terms → suggested follow-up queries
    mesh_counter: dict[str, int] = {}
    for art in pm_result.articles:
        for term in art.mesh_terms:
            mesh_counter[term] = mesh_counter.get(term, 0) + 1
    top_mesh = sorted(mesh_counter, key=lambda t: (-mesh_counter[t], t))[:LITERATURE_MESH_TOP_TERMS]

    # 3) Topic search OpenNeuro + NeuroVault in parallel (keyword match)
    on_input = SearchOpenNeuroInput(query=params.research_question, modality=params.modality, max_results=10)
    nv_input = SearchNeuroVaultCollectionsInput(query=params.research_question, max_results=10)
    on_result, nv_result = await asyncio.gather(
        search_openneuro_datasets(on_input, openneuro),
        search_neurovault_collections(nv_input, neurovault),
    )

    # 4) DOI link top PubMed papers → NeuroVault collections (the real cross-source step)
    nv_index = await neurovault.get_index()
    sem = asyncio.Semaphore(DOI_LOOKUP_CONCURRENCY)

    async def doi_link(article: PubMedArticle) -> list[dict[str, Any]]:
        if not article.doi:
            return []
        async with sem:
            return _doi_match_collections(nv_index, article.doi)

    linked_matches_per_article = await asyncio.gather(
        *[doi_link(a) for a in pm_result.articles]
    )

    # Merge DOI-linked + keyword-matched NV collections, dedup by id, prefer doi_exact.
    nv_by_id: dict[int, NeuroVaultCollection] = {
        c.collection_id: c for c in nv_result.collections
    }
    evidence: dict[str, str] = {}
    for c in nv_result.collections:
        evidence[_nv_key(c.collection_id)] = "keyword_match"
    for batch in linked_matches_per_article:
        for proj in batch:
            cid = int(proj.get("id") or 0)
            if cid == 0:
                continue
            if cid not in nv_by_id:
                nv_by_id[cid] = _collection_from_projection(proj)
            evidence[_nv_key(cid)] = "doi_exact"  # upgrade evidence to DOI-confirmed

    merged_nv = list(nv_by_id.values())[:20]  # cap to keep response bounded

    # Evidence labels for the other sources
    for d in on_result.datasets:
        evidence[_on_key(d.accession_number)] = "keyword_match"
    for a in pm_result.articles:
        evidence[_pm_key(a.pmid)] = "keyword_match"

    # Suggested follow-ups
    suggestions: list[str] = []
    for term in top_mesh:
        suggestions.append(f"Search OpenNeuro for datasets related to '{term}'.")
    for a in pm_result.articles[:2]:
        suggestions.append(
            f"Find NeuroVault maps for paper: find_neurovault_maps_for_paper(pmid='{a.pmid}')"
        )
        suggestions.append(
            f"Find related papers: find_related_pubmed_articles(pmid='{a.pmid}')"
        )

    doi_linked_count = sum(1 for v in evidence.values() if v == "doi_exact" and v.startswith("neurovault"))
    doi_linked_count = sum(
        1 for k, v in evidence.items() if k.startswith("neurovault_collection:") and v == "doi_exact"
    )

    return CrossSourceResult(
        query=params.research_question,
        pubmed_articles=pm_result.articles,
        openneuro_datasets=on_result.datasets,
        neurovault_collections=merged_nv,
        suggested_next_queries=suggestions[:8],
        linkage_evidence=evidence,
        notes=(
            f"PubMed: {pm_result.returned}/{pm_result.total_hits} hits. "
            f"OpenNeuro: {on_result.total_returned} keyword matches. "
            f"NeuroVault: {len(nv_result.collections)} keyword matches + {doi_linked_count} "
            f"DOI-confirmed link(s) from the top {LITERATURE_TOP_PAPERS} PubMed papers. "
            "See `linkage_evidence` for per-result confidence."
        ),
    )


def _doi_match_collections(index: list[dict[str, Any]], doi: str) -> list[dict[str, Any]]:
    """Case-insensitive DOI match against both `DOI` and `preprint_DOI` fields."""
    target = doi.lower()
    return [
        p for p in index
        if ((p.get("DOI") or "").lower() == target) or ((p.get("preprint_DOI") or "").lower() == target)
    ]


__all__ = [
    "find_papers_using_dataset",
    "find_neurovault_maps_for_paper",
    "find_datasets_for_topic",
    "comprehensive_literature_search",
]
