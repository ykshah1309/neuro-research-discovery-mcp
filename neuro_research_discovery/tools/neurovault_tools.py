"""Family B — NeuroVault tools.

The keyword/DOI/modality filtering all happens client-side against the cached
collection index (see clients/neurovault.py — the upstream API ignores filters).
"""

from __future__ import annotations

import asyncio
from typing import Any

import time

from .. import settings
from ..clients.neurovault import NeuroVaultClient
from ..disk_cache import (
    NEUROVAULT_INDEX_SCHEMA_VERSION,
    _index_path,
    is_fresh,
    is_serveable,
    load_neurovault_index,
)
from ..models import (
    GetNeuroVaultCollectionInput,
    GetNeuroVaultCollectionPublicationsInput,
    GetNeuroVaultImageInput,
    NeuroVaultCacheStatus,
    NeuroVaultCacheStatusInput,
    NeuroVaultCollection,
    NeuroVaultCollectionPublications,
    NeuroVaultCollectionSearchResult,
    NeuroVaultImage,
    NeuroVaultImageSearchResult,
    PrewarmNeuroVaultIndexInput,
    PrewarmReport,
    SearchNeuroVaultCollectionsInput,
    SearchNeuroVaultImagesInput,
)
from ..text_safety import MAX_AUTHORS_LEN, MAX_TITLE_LEN, make_untrusted

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


def _wrap_or_none(value, source: str, max_len: int):
    """Wrap a string-or-None in UntrustedText, preserving None."""
    if value is None or value == "":
        return None
    return make_untrusted(value, source=source, max_len=max_len)


def _collection_from_projection(p: dict[str, Any]) -> NeuroVaultCollection:
    cid = int(p.get("id") or 0)
    return NeuroVaultCollection(
        collection_id=cid,
        name=make_untrusted(p.get("name") or "", source="neurovault", max_len=MAX_TITLE_LEN),
        description=make_untrusted(p.get("description") or "", source="neurovault"),
        doi=p.get("DOI") or None,
        preprint_doi=p.get("preprint_DOI") or None,
        authors=_wrap_or_none(p.get("authors"), "neurovault", MAX_AUTHORS_LEN),
        journal_name=_wrap_or_none(p.get("journal_name"), "neurovault", MAX_TITLE_LEN),
        paper_url=p.get("paper_url"),
        num_images=int(p.get("number_of_images") or 0),
        download_url=p.get("download_url") or f"https://neurovault.org/collections/{cid}/download",
    )


def _collection_from_full(c: dict[str, Any]) -> NeuroVaultCollection:
    cid = int(c.get("id") or 0)
    return NeuroVaultCollection(
        collection_id=cid,
        name=make_untrusted(c.get("name") or "", source="neurovault", max_len=MAX_TITLE_LEN),
        description=make_untrusted(c.get("description") or "", source="neurovault"),
        doi=c.get("DOI"),
        preprint_doi=c.get("preprint_DOI"),
        authors=_wrap_or_none(c.get("authors"), "neurovault", MAX_AUTHORS_LEN),
        journal_name=_wrap_or_none(c.get("journal_name"), "neurovault", MAX_TITLE_LEN),
        paper_url=c.get("paper_url"),
        num_images=int(c.get("number_of_images") or 0),
        download_url=c.get("download_url") or f"https://neurovault.org/collections/{cid}/download",
    )


def _image_model(i: dict[str, Any]) -> NeuroVaultImage:
    return NeuroVaultImage(
        image_id=int(i.get("id") or 0),
        name=make_untrusted(i.get("name") or "", source="neurovault", max_len=MAX_TITLE_LEN),
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


async def get_neurovault_cache_status(
    _: NeuroVaultCacheStatusInput, client: NeuroVaultClient
) -> NeuroVaultCacheStatus:
    """Report whether the on-disk and in-memory NeuroVault index are warm.

    Inspects on-disk state first (so the answer is correct even right after a
    fresh process start), then layers in-memory state on top.
    """
    in_mem_loaded = getattr(client, "_index", None) is not None
    partial = bool(getattr(client, "index_partial", False))

    entry = load_neurovault_index()
    on_disk = entry is not None
    age_seconds: int | None = None
    collection_count: int | None = None
    size_bytes: int | None = None
    schema_version: int | None = None
    status: str = "missing"
    notes_parts: list[str] = []

    if entry is not None:
        built_at = float(entry.get("built_at") or 0)
        age_seconds = max(0, int(time.time() - built_at))
        collection_count = len(entry.get("projections") or [])
        schema_version = entry.get("schema_version")
        path = _index_path()
        if path.is_file():
            size_bytes = path.stat().st_size
        if is_fresh(entry):
            status = "fresh"
        elif is_serveable(entry):
            status = "stale_but_serveable"
            notes_parts.append(
                "Cache is past TTL but within 2x; stale-while-revalidate is in effect."
            )
        else:
            status = "expired"
            notes_parts.append("Cache is older than 2x TTL; next call will rebuild.")
    elif in_mem_loaded:
        collection_count = len(client._index or [])  # type: ignore[union-attr]
        status = "fresh"  # in-memory exists; safe to serve
        notes_parts.append(
            "In-memory index exists but no disk file is present. Next server restart will rebuild."
        )
    else:
        notes_parts.append(
            "No NeuroVault index in memory or on disk. First search will trigger a "
            "~2–3 min cold build. Use prewarm_neurovault_index to do this proactively."
        )

    if partial:
        notes_parts.append("Last build was partial — some upstream pages failed.")

    return NeuroVaultCacheStatus(
        status=status,  # type: ignore[arg-type]
        in_memory_loaded=in_mem_loaded,
        on_disk_present=on_disk,
        age_seconds=age_seconds,
        ttl_seconds=settings.NEUROVAULT_INDEX_TTL,
        collection_count=collection_count,
        partial=partial,
        size_bytes=size_bytes,
        schema_version=schema_version,
        notes=" ".join(notes_parts) if notes_parts else "Cache is healthy.",
    )


async def prewarm_neurovault_index(
    params: PrewarmNeuroVaultIndexInput, client: NeuroVaultClient
) -> PrewarmReport:
    """Trigger an index build if needed (or always with force_refresh=True).

    Use at the start of a research session so the agent doesn't pay the
    cold-build cost on the first real search. If the cache is already fresh
    (and force_refresh is false) we return immediately with action='already_fresh_skipped'.
    """
    if not params.force_refresh:
        entry = load_neurovault_index()
        if entry is not None and is_fresh(entry):
            return PrewarmReport(
                action="already_fresh_skipped",
                elapsed_seconds=0.0,
                collection_count=len(entry.get("projections") or []),
                partial=bool(entry.get("partial", False)),
                notes="Cache is already fresh; nothing to do.",
            )

    t0 = time.monotonic()
    try:
        index = await client.get_index(force_refresh=True)
        elapsed = time.monotonic() - t0
        return PrewarmReport(
            action="rebuilt",
            elapsed_seconds=round(elapsed, 2),
            collection_count=len(index),
            partial=bool(getattr(client, "index_partial", False)),
            notes=(
                f"Rebuild completed in {elapsed:.1f}s. Index persisted to disk; "
                "subsequent restarts will load instantly."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        return PrewarmReport(
            action="rebuild_failed",
            elapsed_seconds=round(elapsed, 2),
            collection_count=0,
            partial=True,
            notes=f"Rebuild failed after {elapsed:.1f}s: {type(exc).__name__}: {exc}",
        )


async def get_neurovault_collection_publications(
    params: GetNeuroVaultCollectionPublicationsInput, client: NeuroVaultClient
) -> NeuroVaultCollectionPublications:
    raw = await client.get_collection(params.collection_id)
    return NeuroVaultCollectionPublications(
        collection_id=params.collection_id,
        doi=raw.get("DOI"),
        preprint_doi=raw.get("preprint_DOI"),
        paper_url=raw.get("paper_url"),
        journal_name=_wrap_or_none(raw.get("journal_name"), "neurovault", MAX_TITLE_LEN),
        authors=_wrap_or_none(raw.get("authors"), "neurovault", MAX_AUTHORS_LEN),
    )
