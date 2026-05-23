"""MCP server entry point.

Wires 17 typed tools across the four families to the MCP stdio transport. All
upstream errors are caught and returned as a structured ToolError JSON inside
TextContent — exceptions never propagate to the client.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from pydantic import BaseModel, ValidationError

from . import __version__
from .clients.neurovault import NeuroVaultClient
from .clients.openneuro import OpenNeuroClient
from .clients.pubmed import PubMedClient
from .errors import ToolError, classify_exception
from .models import (
    ComprehensiveLiteratureSearchInput,
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
    SearchNeuroVaultCollectionsInput,
    SearchNeuroVaultImagesInput,
    SearchOpenNeuroInput,
    SearchPubMedInput,
)
from .tools import bridge_tools, neurovault_tools, openneuro_tools, pubmed_tools

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


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return [
        # ---- Family A: OpenNeuro ----
        types.Tool(
            name="search_openneuro_datasets",
            description=(
                "Search OpenNeuro for BIDS-formatted neuroimaging datasets by keyword. "
                "Optional top-level modality filter ('mri', 'eeg', 'meg', 'ieeg', 'pet', 'nirs'). "
                "Returns lightweight summaries (accession, title, modalities, subject count, tasks)."
            ),
            inputSchema=_schema(SearchOpenNeuroInput),
        ),
        types.Tool(
            name="get_openneuro_dataset",
            description=(
                "Get full metadata for one OpenNeuro dataset by accession (e.g. 'ds000001'): "
                "title, description excerpt, modalities, subject/session counts, tasks, species, "
                "download URL, associated paper DOIs."
            ),
            inputSchema=_schema(GetOpenNeuroDatasetInput),
        ),
        types.Tool(
            name="list_openneuro_dataset_files",
            description=(
                "List files in the latest snapshot of an OpenNeuro dataset. Optional `modality` "
                "filter (e.g. 'anat', 'func', 'dwi') descends into per-subject modality directories."
            ),
            inputSchema=_schema(ListOpenNeuroDatasetFilesInput),
        ),
        types.Tool(
            name="get_openneuro_dataset_publications",
            description=(
                "Get DOIs and reference links associated with an OpenNeuro dataset: the dataset's "
                "own DOI plus any author-supplied paper DOIs and reference URLs."
            ),
            inputSchema=_schema(GetOpenNeuroPublicationsInput),
        ),

        # ---- Family B: NeuroVault ----
        types.Tool(
            name="search_neurovault_collections",
            description=(
                "Search NeuroVault collections by keyword (matches name, description, authors, "
                "journal). NOTE: NeuroVault has no server-side search; this MCP maintains a cached "
                "index built on first call (~30–60s warm-up, sub-millisecond thereafter)."
            ),
            inputSchema=_schema(SearchNeuroVaultCollectionsInput),
        ),
        types.Tool(
            name="search_neurovault_images",
            description=(
                "Search NeuroVault images by keyword, optionally filtered by `modality` "
                "(e.g. 'fMRI-BOLD', 'Diffusion MRI') and `map_type` (e.g. 'Z map', 'T map'). "
                "Looks up images via the parent collections that match the keyword."
            ),
            inputSchema=_schema(SearchNeuroVaultImagesInput),
        ),
        types.Tool(
            name="get_neurovault_collection",
            description="Get a single NeuroVault collection by integer ID.",
            inputSchema=_schema(GetNeuroVaultCollectionInput),
        ),
        types.Tool(
            name="get_neurovault_image_metadata",
            description="Get full metadata for a single NeuroVault image by integer ID.",
            inputSchema=_schema(GetNeuroVaultImageInput),
        ),
        types.Tool(
            name="get_neurovault_collection_publications",
            description=(
                "Get the publication metadata associated with a NeuroVault collection: DOI, "
                "preprint DOI, paper URL, journal, authors."
            ),
            inputSchema=_schema(GetNeuroVaultCollectionPublicationsInput),
        ),

        # ---- Family C: PubMed ----
        types.Tool(
            name="search_pubmed",
            description=(
                "Search PubMed (NCBI) for biomedical literature by query. Optionally restrict to "
                "the last N years. Returns total hit count plus full article records (title, "
                "authors, journal, year, abstract, DOI, MeSH terms)."
            ),
            inputSchema=_schema(SearchPubMedInput),
        ),
        types.Tool(
            name="get_pubmed_article",
            description="Fetch a single PubMed article by PMID with full metadata and abstract.",
            inputSchema=_schema(GetPubMedArticleInput),
        ),
        types.Tool(
            name="get_pubmed_article_abstract",
            description="Fetch just the title + abstract for a PubMed article by PMID (lightweight).",
            inputSchema=_schema(GetPubMedAbstractInput),
        ),
        types.Tool(
            name="find_related_pubmed_articles",
            description=(
                "Use NCBI's similarity index (elink pubmed_pubmed) to find articles related to "
                "a given PMID. Returns related PMIDs plus their full article records."
            ),
            inputSchema=_schema(FindRelatedPubMedInput),
        ),

        # ---- Family D: Bridge ----
        types.Tool(
            name="find_papers_using_dataset",
            description=(
                "Cross-source: given an OpenNeuro dataset accession, find PubMed articles that "
                "are linked to it via DOI. Combines OpenNeuro metadata with PubMed records into "
                "a unified result."
            ),
            inputSchema=_schema(FindPapersUsingDatasetInput),
        ),
        types.Tool(
            name="find_neurovault_maps_for_paper",
            description=(
                "Cross-source: given a PubMed PMID, find NeuroVault collections that link to the "
                "paper's DOI (statistical maps published alongside the paper)."
            ),
            inputSchema=_schema(FindNeuroVaultMapsForPaperInput),
        ),
        types.Tool(
            name="find_datasets_for_topic",
            description=(
                "Cross-source: search both OpenNeuro and NeuroVault in parallel for a research "
                "topic. Useful when you want raw data AND derived maps for the same question."
            ),
            inputSchema=_schema(FindDatasetsForTopicInput),
        ),
        types.Tool(
            name="comprehensive_literature_search",
            description=(
                "Cross-source omnibus: PubMed search → extract MeSH terms from top papers → "
                "search OpenNeuro and NeuroVault in parallel with the same question → return a "
                "unified result with suggested follow-up tool calls."
            ),
            inputSchema=_schema(ComprehensiveLiteratureSearchInput),
        ),
    ]


def _ok(model: BaseModel) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=model.model_dump_json(indent=2))]


def _err(err: ToolError) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=err.model_dump_json(indent=2))]


# A dispatch table keeps the call_tool body small and testable.
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
async def _call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return _ok(result)
    except ValidationError as exc:
        return _err(ToolError(
            error_type="bad_input",
            human_readable_message=f"Input validation failed: {exc}",
            suggested_action="Check the tool's inputSchema and re-issue.",
        ))
    except Exception as exc:  # noqa: BLE001 — convert every exception to ToolError
        return _err(classify_exception(exc))


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
