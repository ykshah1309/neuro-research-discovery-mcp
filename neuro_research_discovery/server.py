"""MCP server entry point.

Wires 19 typed tools (4 OpenNeuro + 7 NeuroVault + 4 PubMed + 4 bridge) to the MCP stdio transport.

MCP shape compliance (v0.3, spec rev 2025-06-18+):
- Every tool declares both `inputSchema` and `outputSchema` (built from Pydantic).
- Every successful response returns `(content, structuredContent)` so legacy
  clients see TextContent JSON and modern clients see validated structured data.
- Tool errors return `CallToolResult` with `isError=True` plus a structured
  `ToolError` payload (also in both shapes).
- Every tool carries ToolAnnotations: read-only (no upstream mutations),
  open-world (depends on external services), idempotent (cached).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from pydantic import BaseModel, ValidationError

from . import __version__
from .cache import cache_stats
from .clients.neurovault import NeuroVaultClient
from .clients.openneuro import OpenNeuroClient
from .clients.pubmed import PubMedClient
from .errors import ToolError, classify_exception
from .models import (
    ComprehensiveLiteratureSearchInput,
    CrossSourceResult,
    FindDatasetsForTopicInput,
    FindNeuroVaultMapsForPaperInput,
    FindPapersUsingDatasetInput,
    FindRelatedPubMedInput,
    GetNeuroVaultCollectionInput,
    GetNeuroVaultCollectionPublicationsInput,
    GetNeuroVaultImageInput,
    GetOpenNeuroDatasetInput,
    GetOpenNeuroPublicationsInput,
    GetPubMedAbstractInput,
    GetPubMedArticleInput,
    ListOpenNeuroDatasetFilesInput,
    NeuroVaultCacheStatus,
    NeuroVaultCacheStatusInput,
    NeuroVaultCollection,
    NeuroVaultCollectionPublications,
    NeuroVaultCollectionSearchResult,
    NeuroVaultImage,
    NeuroVaultImageSearchResult,
    OpenNeuroDataset,
    OpenNeuroFileListing,
    OpenNeuroPublications,
    OpenNeuroSearchResult,
    PrewarmNeuroVaultIndexInput,
    PrewarmReport,
    PubMedAbstract,
    PubMedArticle,
    PubMedRelatedResult,
    PubMedSearchResult,
    SearchNeuroVaultCollectionsInput,
    SearchNeuroVaultImagesInput,
    SearchOpenNeuroInput,
    SearchPubMedInput,
)
from .tools import bridge_tools, neurovault_tools, openneuro_tools, pubmed_tools

# Audit logger emits one JSON-line per tool call. Goes to stderr (which is
# the MCP-server-side log channel since stdout is the protocol stream).
audit_log = logging.getLogger("neuro_research_discovery.audit")
if not audit_log.handlers:
    _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit_log.addHandler(_audit_handler)
    audit_log.setLevel(logging.INFO)
    audit_log.propagate = False

server: Server = Server("neuro-research-discovery-mcp")

# Shared client instances. Initialized lazily on first tool call so the server
# can start without immediately opening upstream connections.
_clients: dict[str, Any] = {}


def _openneuro() -> OpenNeuroClient:
    c = _clients.get("openneuro")
    if c is None:
        c = OpenNeuroClient()
        _clients["openneuro"] = c
    return c


def _neurovault() -> NeuroVaultClient:
    c = _clients.get("neurovault")
    if c is None:
        c = NeuroVaultClient()
        _clients["neurovault"] = c
    return c


def _pubmed() -> PubMedClient:
    c = _clients.get("pubmed")
    if c is None:
        c = PubMedClient()
        _clients["pubmed"] = c
    return c


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


_READONLY_OPEN_WORLD = types.ToolAnnotations(
    title=None,
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _tool(name: str, description: str, in_model: type[BaseModel], out_model: type[BaseModel]) -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        inputSchema=_schema(in_model),
        outputSchema=_schema(out_model),
        annotations=_READONLY_OPEN_WORLD,
    )


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return [
        # ---- Family A: OpenNeuro ----
        _tool(
            "search_openneuro_datasets",
            "Search OpenNeuro for BIDS-formatted neuroimaging datasets by keyword. "
            "Optional top-level modality filter ('mri', 'eeg', 'meg', 'ieeg', 'pet', 'nirs'). "
            "Returns lightweight summaries (accession, title, modalities, subject count, tasks).",
            SearchOpenNeuroInput, OpenNeuroSearchResult,
        ),
        _tool(
            "get_openneuro_dataset",
            "Get full metadata for one OpenNeuro dataset by accession (e.g. 'ds000001'): "
            "title, description excerpt (UntrustedText envelope), modalities, subject/session counts, "
            "tasks, species, download URL, associated paper DOIs (normalized).",
            GetOpenNeuroDatasetInput, OpenNeuroDataset,
        ),
        _tool(
            "list_openneuro_dataset_files",
            "List files in the latest snapshot of an OpenNeuro dataset. Optional `modality` "
            "filter (anat / func / dwi / ...) uses files(recursive: true) for a single-call walk. "
            "Listings are capped at 200 entries; `truncated` flag reports when capped.",
            ListOpenNeuroDatasetFilesInput, OpenNeuroFileListing,
        ),
        _tool(
            "get_openneuro_dataset_publications",
            "Get DOIs and reference links associated with an OpenNeuro dataset: the dataset's "
            "own DOI plus any author-supplied paper DOIs and reference URLs.",
            GetOpenNeuroPublicationsInput, OpenNeuroPublications,
        ),

        # ---- Family B: NeuroVault ----
        _tool(
            "search_neurovault_collections",
            "Search NeuroVault collections by keyword (matches name, description, authors, "
            "journal). NOTE: NeuroVault has no server-side search; this MCP maintains a cached "
            "index. First-ever cold build ~2–3 min; subsequent server restarts load from disk in ~100 ms.",
            SearchNeuroVaultCollectionsInput, NeuroVaultCollectionSearchResult,
        ),
        _tool(
            "search_neurovault_images",
            "Search NeuroVault images by keyword, optionally filtered by `modality` "
            "(e.g. 'fMRI-BOLD', 'Diffusion MRI') and `map_type` (e.g. 'Z map', 'T map'). "
            "Looks up images via the parent collections that match the keyword.",
            SearchNeuroVaultImagesInput, NeuroVaultImageSearchResult,
        ),
        _tool(
            "get_neurovault_collection",
            "Get a single NeuroVault collection by integer ID.",
            GetNeuroVaultCollectionInput, NeuroVaultCollection,
        ),
        _tool(
            "get_neurovault_image_metadata",
            "Get full metadata for a single NeuroVault image by integer ID.",
            GetNeuroVaultImageInput, NeuroVaultImage,
        ),
        _tool(
            "get_neurovault_collection_publications",
            "Get the publication metadata associated with a NeuroVault collection: DOI, "
            "preprint DOI, paper URL, journal, authors.",
            GetNeuroVaultCollectionPublicationsInput, NeuroVaultCollectionPublications,
        ),
        _tool(
            "get_neurovault_cache_status",
            "Report the current state of the NeuroVault collection index cache: "
            "fresh / stale / missing, age in seconds, collection count, on-disk size. "
            "Use this to decide whether to call prewarm_neurovault_index before a "
            "research session.",
            NeuroVaultCacheStatusInput, NeuroVaultCacheStatus,
        ),
        _tool(
            "prewarm_neurovault_index",
            "Proactively build (or rebuild with force_refresh=true) the NeuroVault "
            "collection index. First-ever cold build takes ~2–3 minutes; subsequent "
            "restarts load from disk in ~100 ms. Returns immediately if the cache is "
            "already fresh.",
            PrewarmNeuroVaultIndexInput, PrewarmReport,
        ),

        # ---- Family C: PubMed ----
        _tool(
            "search_pubmed",
            "Search PubMed (NCBI) for biomedical literature by query. Optionally restrict to "
            "the last N years. Returns total hit count plus full article records (title, "
            "authors, journal, year, abstract in UntrustedText envelope, DOI, MeSH terms). "
            "Use include_abstracts=false to omit abstracts for lighter responses.",
            SearchPubMedInput, PubMedSearchResult,
        ),
        _tool(
            "get_pubmed_article",
            "Fetch a single PubMed article by PMID with full metadata and abstract.",
            GetPubMedArticleInput, PubMedArticle,
        ),
        _tool(
            "get_pubmed_article_abstract",
            "Fetch just the title + abstract for a PubMed article by PMID (lightweight).",
            GetPubMedAbstractInput, PubMedAbstract,
        ),
        _tool(
            "find_related_pubmed_articles",
            "Use NCBI's similarity index (elink pubmed_pubmed) to find articles related to "
            "a given PMID. Returns related PMIDs plus their full article records.",
            FindRelatedPubMedInput, PubMedRelatedResult,
        ),

        # ---- Family D: Bridge ----
        _tool(
            "find_papers_using_dataset",
            "Cross-source: given an OpenNeuro dataset accession, find PubMed articles linked "
            "via DOI. Returns CrossSourceResult with linkage_evidence labels for each match.",
            FindPapersUsingDatasetInput, CrossSourceResult,
        ),
        _tool(
            "find_neurovault_maps_for_paper",
            "Cross-source: given a PubMed PMID, find NeuroVault collections that link to the "
            "paper's DOI (statistical maps published alongside the paper). DOIs are normalized.",
            FindNeuroVaultMapsForPaperInput, CrossSourceResult,
        ),
        _tool(
            "find_datasets_for_topic",
            "Cross-source: parallel keyword search across OpenNeuro and NeuroVault. "
            "Results are labeled `keyword_match` in linkage_evidence — these are leads, "
            "not confirmed links. Use the find_papers_using_dataset and "
            "find_neurovault_maps_for_paper tools to confirm via DOI.",
            FindDatasetsForTopicInput, CrossSourceResult,
        ),
        _tool(
            "comprehensive_literature_search",
            "Cross-source omnibus: PubMed search → extract MeSH terms → topic-search OpenNeuro "
            "and NeuroVault → also resolve top PubMed paper DOIs to NeuroVault collections. "
            "Merged results carry linkage_evidence labels distinguishing DOI-confirmed links "
            "from keyword leads.",
            ComprehensiveLiteratureSearchInput, CrossSourceResult,
        ),
    ]


def _ok_result(model: BaseModel) -> types.CallToolResult:
    structured = model.model_dump(mode="json")
    text_body = model.model_dump_json(indent=2)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text_body)],
        structuredContent=structured,
        isError=False,
    )


def _err_result(err: ToolError) -> types.CallToolResult:
    structured = err.model_dump(mode="json")
    text_body = err.model_dump_json(indent=2)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text_body)],
        structuredContent=structured,
        isError=True,
    )


async def _dispatch(name: str, arguments: dict[str, Any]) -> BaseModel:
    if name == "search_openneuro_datasets":
        return await openneuro_tools.search_openneuro_datasets(
            SearchOpenNeuroInput(**arguments), _openneuro()
        )
    if name == "get_openneuro_dataset":
        return await openneuro_tools.get_openneuro_dataset(
            GetOpenNeuroDatasetInput(**arguments), _openneuro()
        )
    if name == "list_openneuro_dataset_files":
        return await openneuro_tools.list_openneuro_dataset_files(
            ListOpenNeuroDatasetFilesInput(**arguments), _openneuro()
        )
    if name == "get_openneuro_dataset_publications":
        return await openneuro_tools.get_openneuro_dataset_publications(
            GetOpenNeuroPublicationsInput(**arguments), _openneuro()
        )
    if name == "search_neurovault_collections":
        return await neurovault_tools.search_neurovault_collections(
            SearchNeuroVaultCollectionsInput(**arguments), _neurovault()
        )
    if name == "search_neurovault_images":
        return await neurovault_tools.search_neurovault_images(
            SearchNeuroVaultImagesInput(**arguments), _neurovault()
        )
    if name == "get_neurovault_collection":
        return await neurovault_tools.get_neurovault_collection(
            GetNeuroVaultCollectionInput(**arguments), _neurovault()
        )
    if name == "get_neurovault_image_metadata":
        return await neurovault_tools.get_neurovault_image_metadata(
            GetNeuroVaultImageInput(**arguments), _neurovault()
        )
    if name == "get_neurovault_collection_publications":
        return await neurovault_tools.get_neurovault_collection_publications(
            GetNeuroVaultCollectionPublicationsInput(**arguments), _neurovault()
        )
    if name == "get_neurovault_cache_status":
        return await neurovault_tools.get_neurovault_cache_status(
            NeuroVaultCacheStatusInput(**arguments), _neurovault()
        )
    if name == "prewarm_neurovault_index":
        return await neurovault_tools.prewarm_neurovault_index(
            PrewarmNeuroVaultIndexInput(**arguments), _neurovault()
        )
    if name == "search_pubmed":
        return await pubmed_tools.search_pubmed(SearchPubMedInput(**arguments), _pubmed())
    if name == "get_pubmed_article":
        return await pubmed_tools.get_pubmed_article(GetPubMedArticleInput(**arguments), _pubmed())
    if name == "get_pubmed_article_abstract":
        return await pubmed_tools.get_pubmed_article_abstract(
            GetPubMedAbstractInput(**arguments), _pubmed()
        )
    if name == "find_related_pubmed_articles":
        return await pubmed_tools.find_related_pubmed_articles(
            FindRelatedPubMedInput(**arguments), _pubmed()
        )
    if name == "find_papers_using_dataset":
        return await bridge_tools.find_papers_using_dataset(
            FindPapersUsingDatasetInput(**arguments), _openneuro(), _pubmed()
        )
    if name == "find_neurovault_maps_for_paper":
        return await bridge_tools.find_neurovault_maps_for_paper(
            FindNeuroVaultMapsForPaperInput(**arguments), _pubmed(), _neurovault()
        )
    if name == "find_datasets_for_topic":
        return await bridge_tools.find_datasets_for_topic(
            FindDatasetsForTopicInput(**arguments), _openneuro(), _neurovault()
        )
    if name == "comprehensive_literature_search":
        return await bridge_tools.comprehensive_literature_search(
            ComprehensiveLiteratureSearchInput(**arguments), _pubmed(), _openneuro(), _neurovault()
        )
    raise ValueError(f"Unknown tool: {name}")


@server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    t0 = time.monotonic()
    err_type: str | None = None
    is_error = False
    # Per-call cache stats — incremented by AsyncTTLCache as it serves
    # hits/misses, snapshotted before we emit the audit log line.
    stats_token = cache_stats.set({"hits": 0, "misses": 0})
    try:
        try:
            result = await _dispatch(name, arguments)
        except ValidationError as exc:
            err_type = "ValidationError"
            is_error = True
            return _err_result(ToolError(
                error_type="bad_input",
                human_readable_message=f"Input validation failed: {exc}",
                suggested_action="Check the tool's inputSchema and re-issue.",
            ))
        except Exception as exc:  # noqa: BLE001
            err_type = type(exc).__name__
            is_error = True
            return _err_result(classify_exception(exc))
        return _ok_result(result)
    finally:
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 1)
        stats = cache_stats.get() or {"hits": 0, "misses": 0}
        cache_stats.reset(stats_token)
        try:
            audit_log.info(json.dumps({
                "ts": round(time.time(), 3),
                "tool": name,
                "arg_keys": sorted((arguments or {}).keys()),
                "elapsed_ms": elapsed_ms,
                "is_error": is_error,
                "error_type": err_type,
                "cache_hits": stats["hits"],
                "cache_misses": stats["misses"],
            }))
        except Exception:  # noqa: BLE001 — never let logging break the response
            pass


async def _run() -> None:
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="neuro-research-discovery-mcp",
                    server_version=__version__,
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        await asyncio.gather(
            *(c.aclose() for c in _clients.values() if hasattr(c, "aclose")),
            return_exceptions=True,
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
