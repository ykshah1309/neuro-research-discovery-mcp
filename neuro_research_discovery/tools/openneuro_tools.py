"""Family A — OpenNeuro tools.

Each function takes a Pydantic input model and returns a Pydantic output model.
Field extraction lives here because the GraphQL shape is awkward (modalities and
subjects/tasks live in `latestSnapshot.summary`, the canonical title is in
`latestSnapshot.description.Name`, DOIs are scattered across three locations).
"""

from __future__ import annotations

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


def _summary(node: dict[str, Any]) -> OpenNeuroDatasetSummary:
    snap = node.get("latestSnapshot") or {}
    desc = snap.get("description") or {}
    summary = snap.get("summary") or {}
    return OpenNeuroDatasetSummary(
        accession_number=node.get("id", ""),
        title=desc.get("Name") or "",
        modalities=list(summary.get("modalities") or []),
        num_subjects=len(summary.get("subjects") or []),
        tasks=list(summary.get("tasks") or []),
    )


def _download_url(accession: str) -> str:
    # OpenNeuro snapshot download via S3 mirror; this is the user-facing landing page.
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
            cleaned = candidate.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/").removeprefix("doi:")
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

    # Description: prefer the README first line(s); fall back to studyDomain or empty.
    readme = (snap.get("readme") or "").strip()
    description = readme[:500] if readme else (metadata.get("studyDomain") or "")

    return OpenNeuroDataset(
        accession_number=ds.get("id", params.accession_number),
        title=desc.get("Name") or ds.get("name", ""),
        description=description,
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
        # Descend one level into per-subject dirs that contain the requested modality.
        for entry in root_files:
            if entry.get("directory") and entry.get("filename", "").startswith("sub-"):
                sub_files = await client.list_files(
                    params.accession_number, tag, tree=entry.get("id")
                )
                for s in sub_files:
                    name = s.get("filename") or ""
                    if name == params.modality or name.startswith(params.modality + "/"):
                        if s.get("directory"):
                            # Descend one more level to actually list the data files.
                            inner = await client.list_files(
                                params.accession_number, tag, tree=s.get("id")
                            )
                            files.extend(_to_file_models(inner))
                        else:
                            files.append(_to_file_model(s))
    else:
        files = _to_file_models(root_files)

    return OpenNeuroFileListing(
        accession_number=params.accession_number,
        snapshot_tag=tag,
        modality_filter=params.modality,
        files=files,
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
