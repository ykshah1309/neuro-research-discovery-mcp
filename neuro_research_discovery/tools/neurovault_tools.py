"""Family B — NeuroVault tools.

The keyword/DOI/modality filtering all happens client-side against the cached
collection index (see clients/neurovault.py — the upstream API ignores filters).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..clients.neurovault import NeuroVaultClient
from ..models import (
    GetNeuroVaultCollectionInput,
    GetNeuroVaultCollectionPublicationsInput,
    GetNeuroVaultImageInput,
    NeuroVaultCollection,
    NeuroVaultCollectionPublications,
    NeuroVaultCollectionSearchResult,
    NeuroVaultImage,
    NeuroVaultImageSearchResult,
    SearchNeuroVaultCollectionsInput,
    SearchNeuroVaultImagesInput,
)
from ..text_safety import truncate, truncate_authors, truncate_title

# Number of matching collections we'll expand into image results before stopping.
# Caps fan-out for search_neurovault_images.
IMAGE_SEARCH_COLLECTION_CAP = 10


def _matches(record: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return True
    haystack = " ".join(
        str(record.get(f) or "") for f in ("name", "description", "authors", "journal_name")
    ).lower()
    return all(t in haystack for t in terms)


def _collection_from_projection(p: dict[str, Any]) -> NeuroVaultCollection:
    cid = int(p.get("id") or 0)
    return NeuroVaultCollection(
        collection_id=cid,
        name=truncate_title(p.get("name") or ""),
        description=truncate(p.get("description") or ""),
        doi=p.get("DOI") or None,
        preprint_doi=p.get("preprint_DOI") or None,
        authors=truncate_authors(p.get("authors")),
        journal_name=p.get("journal_name"),
        paper_url=p.get("paper_url"),
        num_images=int(p.get("number_of_images") or 0),
        download_url=p.get("download_url") or f"https://neurovault.org/collections/{cid}/download",
    )


def _collection_from_full(c: dict[str, Any]) -> NeuroVaultCollection:
    cid = int(c.get("id") or 0)
    return NeuroVaultCollection(
        collection_id=cid,
        name=truncate_title(c.get("name") or ""),
        description=truncate(c.get("description") or ""),
        doi=c.get("DOI"),
        preprint_doi=c.get("preprint_DOI"),
        authors=truncate_authors(c.get("authors")),
        journal_name=c.get("journal_name"),
        paper_url=c.get("paper_url"),
        num_images=int(c.get("number_of_images") or 0),
        download_url=c.get("download_url") or f"https://neurovault.org/collections/{cid}/download",
    )


def _image_model(i: dict[str, Any]) -> NeuroVaultImage:
    return NeuroVaultImage(
        image_id=int(i.get("id") or 0),
        name=truncate_title(i.get("name") or ""),
        map_type=i.get("map_type"),
        modality=i.get("modality"),
        collection_id=int(i.get("collection_id") or 0),
        file_url=i.get("file"),
        smoothness_fwhm=i.get("smoothness_fwhm"),
        analysis_level=i.get("analysis_level"),
        image_type=i.get("image_type"),
        is_thresholded=i.get("is_thresholded"),
        cognitive_paradigm=i.get("cognitive_paradigm_cogatlas"),
    )


async def search_neurovault_collections(
    params: SearchNeuroVaultCollectionsInput, client: NeuroVaultClient
) -> NeuroVaultCollectionSearchResult:
    index = await client.get_index()
    partial = bool(getattr(client, "index_partial", False))
    terms = [t for t in params.query.lower().split() if t]
    matches = [p for p in index if _matches(p, terms)][: params.max_results]
    return NeuroVaultCollectionSearchResult(
        query=params.query,
        total_returned=len(matches),
        collections=[_collection_from_projection(p) for p in matches],
        index_partial=partial,
        index_note=(
            "Collection index was built from a partial scan (some pages failed); "
            "results may be incomplete." if partial else None
        ),
    )


async def search_neurovault_images(
    params: SearchNeuroVaultImagesInput, client: NeuroVaultClient
) -> NeuroVaultImageSearchResult:
    """Keyword-search collections first (cheap), then list images per matching
    collection (concurrent, bounded), then filter on modality and map_type."""
    index = await client.get_index()
    terms = [t for t in params.query.lower().split() if t]
    candidate_collections = [p for p in index if _matches(p, terms)][:IMAGE_SEARCH_COLLECTION_CAP]

    if not candidate_collections:
        return NeuroVaultImageSearchResult(
            query=params.query,
            modality=params.modality,
            map_type=params.map_type,
            total_returned=0,
            images=[],
        )

    sem = asyncio.Semaphore(4)

    async def fetch_images(cid: int) -> list[dict[str, Any]]:
        async with sem:
            return await client.list_collection_images(cid, max_results=200)

    image_batches = await asyncio.gather(
        *[fetch_images(int(c["id"])) for c in candidate_collections if c.get("id")]
    )

    images: list[NeuroVaultImage] = []
    for batch in image_batches:
        for img in batch:
            if params.modality and (img.get("modality") or "").lower() != params.modality.lower():
                continue
            if params.map_type and (img.get("map_type") or "").lower() != params.map_type.lower():
                continue
            images.append(_image_model(img))
            if len(images) >= params.max_results:
                break
        if len(images) >= params.max_results:
            break

    return NeuroVaultImageSearchResult(
        query=params.query,
        modality=params.modality,
        map_type=params.map_type,
        total_returned=len(images),
        images=images[: params.max_results],
    )


async def get_neurovault_collection(
    params: GetNeuroVaultCollectionInput, client: NeuroVaultClient
) -> NeuroVaultCollection:
    raw = await client.get_collection(params.collection_id)
    return _collection_from_full(raw)


async def get_neurovault_image_metadata(
    params: GetNeuroVaultImageInput, client: NeuroVaultClient
) -> NeuroVaultImage:
    raw = await client.get_image(params.image_id)
    return _image_model(raw)


async def get_neurovault_collection_publications(
    params: GetNeuroVaultCollectionPublicationsInput, client: NeuroVaultClient
) -> NeuroVaultCollectionPublications:
    raw = await client.get_collection(params.collection_id)
    return NeuroVaultCollectionPublications(
        collection_id=params.collection_id,
        doi=raw.get("DOI"),
        preprint_doi=raw.get("preprint_DOI"),
        paper_url=raw.get("paper_url"),
        journal_name=raw.get("journal_name"),
        authors=raw.get("authors"),
    )
