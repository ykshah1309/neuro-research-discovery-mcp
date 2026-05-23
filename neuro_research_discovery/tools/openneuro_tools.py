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
from ..text_safety import (
    MAX_FILES_PER_LISTING,
    cap_list,
    truncate,
    truncate_title,
)


def _summary(node: dict[str, Any]) -> OpenNeuroDatasetSummary:
    snap = node.get("latestSnapshot") or {}
    desc = snap.get("description") or {}
    summary = snap.get("summary") or {}
    return OpenNeuroDatasetSummary(
        accession_number=node.get("id", ""),
        title=truncate_title(desc.get("Name") or ""),
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
        if candidate and isinstance(candidate, str) and "/" in candidate and " " not in candidate:
            cleaned = (
                candidate.strip()
                .removeprefix("https://doi.org/")
                .removeprefix("http://doi.org/")
                .removeprefix("doi:")
            )
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
        title=truncate_title(desc.get("Name") or ds.get("name", "")),
        description=truncate(description),
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
    ds = await client.get_dataset(params.accession_number)
    snap = ds.get("latestSnapshot") or {}
    tag = snap.get("tag") or ""
    root_files = await client.list_files(params.accession_number, tag, tree=None)

    files: list[OpenNeuroFile] = []
    if params.modality:
        files = await _walk_modality(client, params.accession_number, tag, root_files, params.modality)
    else:
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


async def _walk_modality(
    client: OpenNeuroClient,
    accession: str,
    tag: str,
    root_files: list[dict[str, Any]],
    modality: str,
) -> list[OpenNeuroFile]:
    """Descend into per-subject modality directories, in parallel.

    Bounded concurrency keeps us within the OpenNeuro rate budget (10/sec)
    while cutting wall time on large datasets significantly.
    """
    subject_dirs = [
        e for e in root_files
        if e.get("directory") and (e.get("filename") or "").startswith("sub-")
    ]
    sem = asyncio.Semaphore(4)

    async def for_subject(sub: dict[str, Any]) -> list[OpenNeuroFile]:
        async with sem:
            sub_files = await client.list_files(accession, tag, tree=sub.get("id"))
        out: list[OpenNeuroFile] = []
        for s in sub_files:
            name = s.get("filename") or ""
            if name == modality or name.startswith(modality + "/"):
                if s.get("directory"):
                    async with sem:
                        inner = await client.list_files(accession, tag, tree=s.get("id"))
                    out.extend(_to_file_models(inner))
                else:
                    out.append(_to_file_model(s))
        return out

    batches = await asyncio.gather(*[for_subject(s) for s in subject_dirs])
    flat: list[OpenNeuroFile] = []
    for b in batches:
        flat.extend(b)
        if len(flat) >= MAX_FILES_PER_LISTING:
            return flat
    return flat


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
