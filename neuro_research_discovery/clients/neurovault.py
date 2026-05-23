"""NeuroVault REST client.

KEY CONSTRAINT (from API probe): NeuroVault silently ignores every query-string
filter (search, DOI, modality, map_type). Pagination params (`limit`, `offset`)
are the only thing it honors. So *all* keyword/DOI/modality filtering happens
client-side, and we maintain an in-memory index of collection projections to
make this fast.

Index lifecycle (Tier 2 upgrade):
- On first request, try to load a previously persisted index from disk.
  - Fresh (< TTL): serve immediately, skip rebuild.
  - Stale but serveable (< 2x TTL): serve immediately, kick off background refresh.
  - Older or missing: build synchronously now.
- A successful build is persisted to disk so the next process start is fast.
- During a build, per-page failures are tolerated: we keep the projections from
  successful pages and mark the index as `partial=True`. Tool layer surfaces
  this on the response.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .. import settings
from ..cache import AsyncTTLCache
from ..disk_cache import (
    is_fresh,
    is_serveable,
    load_neurovault_index,
    save_neurovault_index,
)
from ..rate_limit import AsyncTokenBucket
from ..retry import upstream_retry

PAGE_SIZE = 500
# Each page is ~1.5 MB and takes ~7 s end-to-end. Concurrency 8 gets the cold
# index build to ~30–60 s; higher hits diminishing returns and risks tripping
# unannounced rate limits.
INDEX_CONCURRENCY = 8

_logger = logging.getLogger("neuro_research_discovery.clients.neurovault")


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
        self._refresh_task: asyncio.Task | None = None
        self.index_partial: bool = False

    async def aclose(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
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
            params = None
        return gathered[:max_results]

    # ---- collection index ----

    async def get_index(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Return the projection index of every collection.

        Lookup order:
        1. In-process memory (fresh): serve.
        2. Disk cache (fresh): load, store in memory, serve.
        3. Disk cache (stale but serveable): serve stale; schedule background refresh.
        4. Otherwise: build synchronously and persist.

        With `force_refresh=True`, skip cache lookups and rebuild now.
        """
        if not force_refresh:
            # 1. In-process
            if self._index is not None and self._index_age() < settings.NEUROVAULT_INDEX_TTL:
                return self._index
            # 2 + 3. Disk
            entry = load_neurovault_index()
            if entry:
                self._index = entry["projections"]
                self._index_built_at = float(entry.get("built_at") or 0.0)
                self.index_partial = bool(entry.get("partial", False))
                if is_fresh(entry):
                    return self._index
                if is_serveable(entry):
                    self._schedule_background_refresh()
                    return self._index
                # Stale beyond 2x TTL → fall through to sync rebuild.

        async with self._index_lock:
            # Recheck once we hold the lock; another caller may have built it.
            if not force_refresh and self._index is not None and self._index_age() < settings.NEUROVAULT_INDEX_TTL:
                return self._index
            projections, partial = await self._build_index()
            self._index = projections
            self._index_built_at = time.monotonic()
            self.index_partial = partial
            save_neurovault_index(projections, settings.NEUROVAULT_INDEX_TTL, partial)
            return self._index

    def _index_age(self) -> float:
        # Monotonic-relative age; used for in-process freshness only. Disk
        # freshness uses wall-clock built_at.
        if self._index_built_at == 0.0:
            return float("inf")
        return time.monotonic() - self._index_built_at

    def _schedule_background_refresh(self) -> None:
        """Kick off a non-blocking refresh if one isn't already running."""
        if self._refresh_task and not self._refresh_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop; serve stale and let next call retry
        self._refresh_task = loop.create_task(self._background_refresh())

    async def _background_refresh(self) -> None:
        try:
            projections, partial = await self._build_index()
            async with self._index_lock:
                self._index = projections
                self._index_built_at = time.monotonic()
                self.index_partial = partial
            save_neurovault_index(projections, settings.NEUROVAULT_INDEX_TTL, partial)
            _logger.info("NeuroVault index refreshed in background (%d collections, partial=%s)",
                         len(projections), partial)
        except Exception as exc:  # noqa: BLE001 — background; never propagate
            _logger.warning("Background NeuroVault index refresh failed: %s", exc)

    async def _build_index(self) -> tuple[list[dict[str, Any]], bool]:
        """Returns (projections, partial)."""
        # Step 1: first page tells us the total count.
        try:
            first = await self._get("collections/", params={"limit": PAGE_SIZE, "offset": 0})
        except Exception as exc:
            _logger.warning("NeuroVault index build: first-page fetch failed: %s", exc)
            return [], True

        count = int(first.get("count") or 0)
        projections: list[dict[str, Any]] = [
            _project_collection(r) for r in first.get("results") or []
        ]
        if count <= PAGE_SIZE:
            return projections, False

        # Step 2: fan out remaining pages with bounded concurrency. Per-page
        # failures are caught — we keep what we got and flag partial.
        offsets = list(range(PAGE_SIZE, count, PAGE_SIZE))
        sem = asyncio.Semaphore(INDEX_CONCURRENCY)
        partial = False

        async def fetch_page(offset: int) -> list[dict[str, Any]]:
            nonlocal partial
            async with sem:
                try:
                    page = await self._get(
                        "collections/", params={"limit": PAGE_SIZE, "offset": offset}
                    )
                    return [_project_collection(r) for r in page.get("results") or []]
                except Exception as exc:  # noqa: BLE001
                    partial = True
                    _logger.warning(
                        "NeuroVault index page offset=%d failed: %s", offset, exc
                    )
                    return []

        page_results = await asyncio.gather(*[fetch_page(o) for o in offsets])
        for page_projs in page_results:
            projections.extend(page_projs)
        return projections, partial


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
