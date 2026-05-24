"""Family A — OpenNeuro tools.

Each function takes a Pydantic input model and returns a Pydantic output model.
Field extraction lives here because the GraphQL shape is awkward (modalities and
subjects/tasks live in `latestSnapshot.summary`, the canonical title is in
`latestSnapshot.description.Name`, DOIs are scattered across three locations).

All upstream-supplied text is run through text_safety.truncate() to bound the
size of any single response — see UPGRADE_PLAN.md Tier 1b.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..clients.openneuro import OpenNeuroClient
from ..models import (
    GetOpenNeuroDatasetInput,
    GetOpenNeuroPublicationsInput,
    ListOpenNeuroDatasetFilesInput,
    OpenNeuroDataset,
    OpenNeuroDatasetSummary,
    OpenNeuroFile,
    OpenNeuroFileListing,
    OpenNeuroPublications,
    OpenNeuroSearchResult,
    SearchOpenNeuroInput,
)
from ..doi import normalize_doi
from ..text_safety import (
    MAX_FILES_PER_LISTING,
    MAX_TITLE_LEN,
    cap_list,
    make_untrusted,
)


def _summary(node: dict[str, Any]) -> OpenNeuroDatasetSummary:
    snap = node.get("latestSnapshot") or {}
    desc = snap.get("description") or {}
    summary = snap.get("summary") or {}
    return OpenNeuroDatasetSummary(
        accession_number=node.get("id", ""),
        title=make_untrusted(desc.get("Name") or "", source="openneuro", max_len=MAX_TITLE_LEN),
        modalities=list(summary.get("modalities") or []),
        num_subjects=len(summary.get("subjects") or []),
        tasks=list(summary.get("tasks") or []),
    )


def _download_url(accession: str) -> str:
    return f"https://openneuro.org/datasets/{accession}/download"


def _collect_dois(dataset: dict[str, Any]) -> list[str]:
    """Extract DOIs from the multiple fields OpenNeuro spreads them across.

    Order: dataset's own DOI (DatasetDOI) first, then any associated/openneuro
    paper DOIs. We filter out empty strings and obviously-non-DOI free text.
    """
    found: list[str] = []
    snap = dataset.get("latestSnapshot") or {}
    desc = snap.get("description") or {}
    metadata = dataset.get("metadata") or {}

    for candidate in (
        desc.get("DatasetDOI"),
        metadata.get("associatedPaperDOI"),
        metadata.get("openneuroPaperDOI"),
    ):
        cleaned = normalize_doi(candidate) if isinstance(candidate, str) else None
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


async def search_openneuro_datasets(
    params: SearchOpenNeuroInput, client: OpenNeuroClient
) -> OpenNeuroSearchResult:
    nodes = await client.search_datasets(
        keywords=params.query,
        modality=params.modality,
        first=params.max_results,
    )
    datasets = [_summary(n) for n in nodes[: params.max_results]]
    return OpenNeuroSearchResult(
        query=params.query,
        modality=params.modality,
        total_returned=len(datasets),
        datasets=datasets,
    )


async def get_openneuro_dataset(
    params: GetOpenNeuroDatasetInput, client: OpenNeuroClient
) -> OpenNeuroDataset:
    ds = await client.get_dataset(params.accession_number)
    snap = ds.get("latestSnapshot") or {}
    desc = snap.get("description") or {}
    summary = snap.get("summary") or {}
    metadata = ds.get("metadata") or {}

    readme = (snap.get("readme") or "").strip()
    description = readme if readme else (metadata.get("studyDomain") or "")

    return OpenNeuroDataset(
        accession_number=ds.get("id", params.accession_number),
        title=make_untrusted(desc.get("Name") or ds.get("name", ""), source="openneuro", max_len=MAX_TITLE_LEN),
        description=make_untrusted(description, source="openneuro"),
        modalities=list(summary.get("modalities") or []),
        num_subjects=len(summary.get("subjects") or []),
        num_sessions=len(summary.get("sessions") or []),
        tasks=list(summary.get("tasks") or []),
        species=metadata.get("species") or "",
        download_url=_download_url(params.accession_number),
        associated_publications=_collect_dois(ds),
    )


async def list_openneuro_dataset_files(
    params: ListOpenNeuroDatasetFilesInput, client: OpenNeuroClient
) -> OpenNeuroFileListing:
    """List files in the latest snapshot of an OpenNeuro dataset.

    When `modality` is set, we use the GraphQL `files(recursive: true)` shape
    (single call returning every file in the snapshot) and filter for paths
    containing `/<modality>/`. This replaces the previous per-subject walk
    which made N calls for N subjects.
    """
    ds = await client.get_dataset(params.accession_number)
    snap = ds.get("latestSnapshot") or {}
    tag = snap.get("tag") or ""

    files: list[OpenNeuroFile]
    if params.modality:
        recursive_files = await client.list_files_recursive(params.accession_number, tag)
        modality_token = f"/{params.modality}/"
        files = [
            _to_file_model(f) for f in recursive_files
            if not f.get("directory") and modality_token in (f.get("filename") or "")
        ]
    else:
        root_files = await client.list_files(params.accession_number, tag, tree=None)
        files = _to_file_models(root_files)

    capped, was_truncated = cap_list(files, max_items=MAX_FILES_PER_LISTING)
    return OpenNeuroFileListing(
        accession_number=params.accession_number,
        snapshot_tag=tag,
        modality_filter=params.modality,
        files=capped,
        truncated=was_truncated,
        truncation_note=(
            f"Listing truncated at {MAX_FILES_PER_LISTING} files; dataset has more. "
            "Use a more specific modality filter or fetch by file path directly."
            if was_truncated else None
        ),
    )


def _to_file_model(entry: dict[str, Any]) -> OpenNeuroFile:
    urls = entry.get("urls") or []
    return OpenNeuroFile(
        filename=entry.get("filename") or "",
        size=int(entry.get("size") or 0),
        directory=bool(entry.get("directory")),
        download_url=urls[0] if urls else None,
    )


def _to_file_models(entries: list[dict[str, Any]]) -> list[OpenNeuroFile]:
    return [_to_file_model(e) for e in entries]


async def get_openneuro_dataset_publications(
    params: GetOpenNeuroPublicationsInput, client: OpenNeuroClient
) -> OpenNeuroPublications:
    ds = await client.get_dataset(params.accession_number)
    snap = ds.get("latestSnapshot") or {}
    desc = snap.get("description") or {}
    metadata = ds.get("metadata") or {}

    dataset_doi = desc.get("DatasetDOI") or None
    paper_dois: list[str] = []
    for candidate in (metadata.get("associatedPaperDOI"), metadata.get("openneuroPaperDOI")):
        if candidate and "/" in str(candidate) and " " not in str(candidate):
            paper_dois.append(str(candidate).strip())
    refs = desc.get("ReferencesAndLinks") or []
    refs = [str(r) for r in refs] if isinstance(refs, list) else [str(refs)]
    return OpenNeuroPublications(
        accession_number=params.accession_number,
        dataset_doi=dataset_doi,
        associated_paper_dois=paper_dois,
        references_and_links=refs,
    )
