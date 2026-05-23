"""Family D — Bridge tools.

These tools chain calls across the three clients. They are the actual value-add
of this MCP — agents get a single tool that resolves a question that would
otherwise take three separate searches and a manual DOI cross-walk.

Fan-out is bounded with asyncio.Semaphore to keep per-call cost predictable.
"""

from __future__ import annotations

import asyncio

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
    OpenNeuroDatasetSummary,
    PubMedArticle,
    SearchNeuroVaultCollectionsInput,
    SearchOpenNeuroInput,
    SearchPubMedInput,
)
from .neurovault_tools import _collection_from_projection, search_neurovault_collections
from .openneuro_tools import _collect_dois, _summary, search_openneuro_datasets
from .pubmed_tools import search_pubmed

# Bounds to keep cost predictable for the most fan-out-heavy tool.
LITERATURE_TOP_PAPERS = 5
LITERATURE_MESH_TOP_TERMS = 5


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
            notes="No DOIs are associated with this dataset on OpenNeuro, so no PubMed lookup was possible.",
        )

    # DOI -> PMID, in parallel.
    sem = asyncio.Semaphore(4)

    async def resolve(doi: str) -> str | None:
        async with sem:
            return await pubmed.doi_to_pmid(doi)

    pmids = [p for p in await asyncio.gather(*[resolve(d) for d in dois]) if p]
    articles_raw = await pubmed.efetch_articles(pmids) if pmids else []
    articles = [PubMedArticle(**r) for r in articles_raw]

    return CrossSourceResult(
        query=params.openneuro_accession,
        openneuro_datasets=[summary],
        pubmed_articles=articles,
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
            notes="PubMed record has no DOI — cannot look up associated NeuroVault collections.",
        )

    index = await neurovault.get_index()
    target = article.doi.lower()
    matches = [
        p for p in index
        if ((p.get("DOI") or "").lower() == target) or ((p.get("preprint_DOI") or "").lower() == target)
    ]
    collections: list[NeuroVaultCollection] = [_collection_from_projection(p) for p in matches]
    return CrossSourceResult(
        query=params.pmid,
        pubmed_articles=[article],
        neurovault_collections=collections,
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

    on_task = search_openneuro_datasets(on_input, openneuro)
    nv_task = search_neurovault_collections(nv_input, neurovault)
    on_result, nv_result = await asyncio.gather(on_task, nv_task)

    return CrossSourceResult(
        query=params.research_topic,
        openneuro_datasets=on_result.datasets,
        neurovault_collections=nv_result.collections,
        notes=(
            f"OpenNeuro returned {on_result.total_returned} dataset(s); "
            f"NeuroVault returned {nv_result.total_returned} collection(s). "
            "Modality filter (if set) applies to OpenNeuro top-level modality only."
        ),
    )


async def comprehensive_literature_search(
    params: ComprehensiveLiteratureSearchInput,
    pubmed: PubMedClient,
    openneuro: OpenNeuroClient,
    neurovault: NeuroVaultClient,
) -> CrossSourceResult:
    # 1) PubMed search
    pm_result = await search_pubmed(
        SearchPubMedInput(query=params.research_question, max_results=LITERATURE_TOP_PAPERS),
        pubmed,
    )

    # 2) MeSH terms from top papers — used as suggested follow-up queries.
    mesh_counter: dict[str, int] = {}
    for art in pm_result.articles:
        for term in art.mesh_terms:
            mesh_counter[term] = mesh_counter.get(term, 0) + 1
    top_mesh = sorted(mesh_counter, key=lambda t: (-mesh_counter[t], t))[:LITERATURE_MESH_TOP_TERMS]

    # 3) Use the research_question to query both data sources in parallel.
    on_input = SearchOpenNeuroInput(query=params.research_question, modality=params.modality, max_results=10)
    nv_input = SearchNeuroVaultCollectionsInput(query=params.research_question, max_results=10)
    on_result, nv_result = await asyncio.gather(
        search_openneuro_datasets(on_input, openneuro),
        search_neurovault_collections(nv_input, neurovault),
    )

    # 4) Suggested follow-ups built from MeSH (keeps suggestions topically grounded).
    suggestions: list[str] = []
    for term in top_mesh:
        suggestions.append(f"Search OpenNeuro for datasets related to '{term}'.")
        suggestions.append(f"Check NeuroVault for {term} maps.")
    if pm_result.articles:
        top = pm_result.articles[0]
        suggestions.append(
            f"Find NeuroVault maps for the top paper: find_neurovault_maps_for_paper(pmid='{top.pmid}')."
        )

    return CrossSourceResult(
        query=params.research_question,
        pubmed_articles=pm_result.articles,
        openneuro_datasets=on_result.datasets,
        neurovault_collections=nv_result.collections,
        suggested_next_queries=suggestions[:8],
        notes=(
            f"Combined search across PubMed ({pm_result.returned} of {pm_result.total_hits} hits), "
            f"OpenNeuro ({on_result.total_returned}), and NeuroVault ({nv_result.total_returned})."
        ),
    )


# Re-export helpers so the server layer doesn't have to import from tool submodules
# just to construct OpenNeuro summaries (used in find_papers_using_dataset response).
__all__ = [
    "find_papers_using_dataset",
    "find_neurovault_maps_for_paper",
    "find_datasets_for_topic",
    "comprehensive_literature_search",
    "OpenNeuroDatasetSummary",
]
