"""NeuroVault REST client.

KEY CONSTRAINT (from API probe): NeuroVault silently ignores every query-string
filter (search, DOI, modality, map_type). Pagination params (`limit`, `offset`)
are the only thing it honors. So *all* keyword/DOI/modality filtering happens
client-side, and we maintain an in-memory index of collection projections to
make this fast.

The collection index is paginated once with concurrency=4 then cached for
NEUROVAULT_INDEX_TTL seconds (default 24 h). On first call this takes a few
seconds; subsequent calls are sub-millisecond.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .. import settings
from ..cache import AsyncTTLCache
from ..rate_limit import AsyncTokenBucket
from ..retry import upstream_retry

PAGE_SIZE = 500
# Each page is ~1.5 MB and takes ~7 s end-to-end. Concurrency 8 gets the cold
# index build to ~30–60 s; concurrency higher than that hits diminishing returns
# (server-side query time dominates) and risks tripping unannounced rate limits.
INDEX_CONCURRENCY = 8


class NeuroVaultClient:
    def __init__(self) -> None:
        self._base = settings.NEUROVAULT_API_BASE.rstrip("/")
        self._bucket = AsyncTokenBucket(rate_per_sec=settings.NEUROVAULT_RATE_PER_SEC)
        self._object_cache = AsyncTTLCache(maxsize=512, ttl=settings.DEFAULT_CACHE_TTL)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.HTTP_CONNECT_TIMEOUT,
                read=settings.HTTP_READ_TIMEOUT,
                write=settings.HTTP_READ_TIMEOUT,
                pool=settings.HTTP_READ_TIMEOUT,
            ),
            headers={"User-Agent": "neuro-research-discovery-mcp/0.1.0"},
        )
        # collection-index cache state
        self._index: list[dict[str, Any]] | None = None
        self._index_built_at: float = 0.0
        self._index_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- low-level GET ----
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._bucket.acquire()
        url = f"{self._base}/{path.lstrip('/')}" if "://" not in path else path
        async for attempt in upstream_retry():
            with attempt:
                resp = await self._http.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        return data

    # ---- per-object endpoints ----

    async def get_collection(self, collection_id: int) -> dict[str, Any]:
        key = f"coll::{collection_id}"
        return await self._object_cache.get_or_set(
            key, lambda: self._get(f"collections/{collection_id}/")
        )

    async def get_image(self, image_id: int) -> dict[str, Any]:
        key = f"img::{image_id}"
        return await self._object_cache.get_or_set(
            key, lambda: self._get(f"images/{image_id}/")
        )

    async def list_collection_images(self, collection_id: int, max_results: int = 100) -> list[dict[str, Any]]:
        """Returns up to max_results images for a collection (paginated server-side)."""
        key = f"coll_imgs::{collection_id}::{max_results}"
        return await self._object_cache.get_or_set(
            key, lambda: self._list_collection_images_uncached(collection_id, max_results)
        )

    async def _list_collection_images_uncached(
        self, collection_id: int, max_results: int
    ) -> list[dict[str, Any]]:
        gathered: list[dict[str, Any]] = []
        url: str | None = f"collections/{collection_id}/images/"
        params: dict[str, Any] | None = {"limit": min(PAGE_SIZE, max_results)}
        while url and len(gathered) < max_results:
            page = await self._get(url, params=params)
            gathered.extend(page.get("results") or [])
            url = page.get("next")
            params = None  # `next` is absolute and already includes pagination
        return gathered[:max_results]

    # ---- collection index (client-side search) ----

    async def get_index(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Returns the projection index of every collection: id, name, description,
        DOI, preprint_DOI, authors, journal_name, number_of_images, paper_url, url.

        Cached for NEUROVAULT_INDEX_TTL seconds.
        """
        now = time.monotonic()
        async with self._index_lock:
            if (
                not force_refresh
                and self._index is not None
                and (now - self._index_built_at) < settings.NEUROVAULT_INDEX_TTL
            ):
                return self._index
            self._index = await self._build_index()
            self._index_built_at = time.monotonic()
            return self._index

    async def _build_index(self) -> list[dict[str, Any]]:
        # Step 1: fetch page 1 to learn count.
        first = await self._get("collections/", params={"limit": PAGE_SIZE, "offset": 0})
        count = int(first.get("count") or 0)
        projections: list[dict[str, Any]] = [_project_collection(r) for r in first.get("results") or []]
        if count <= PAGE_SIZE:
            return projections

        # Step 2: fan out remaining pages with bounded concurrency.
        offsets = list(range(PAGE_SIZE, count, PAGE_SIZE))
        sem = asyncio.Semaphore(INDEX_CONCURRENCY)

        async def fetch_page(offset: int) -> list[dict[str, Any]]:
            async with sem:
                page = await self._get(
                    "collections/", params={"limit": PAGE_SIZE, "offset": offset}
                )
                return [_project_collection(r) for r in page.get("results") or []]

        page_results = await asyncio.gather(*[fetch_page(o) for o in offsets])
        for page_projs in page_results:
            projections.extend(page_projs)
        return projections


def _project_collection(c: dict[str, Any]) -> dict[str, Any]:
    """Keep just the fields we need for search/filtering, to bound memory."""
    return {
        "id": c.get("id"),
        "name": c.get("name") or "",
        "description": c.get("description") or "",
        "DOI": c.get("DOI"),
        "preprint_DOI": c.get("preprint_DOI"),
        "authors": c.get("authors"),
        "journal_name": c.get("journal_name"),
        "paper_url": c.get("paper_url"),
        "number_of_images": c.get("number_of_images") or 0,
        "download_url": c.get("download_url"),
    }
