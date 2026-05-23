"""OpenNeuro GraphQL client.

Raw httpx (no gql dependency overhead). Four queries; each has a small, focused
field selection. Partial-data responses (where some edges have null nodes due to
private datasets being indexed) are normalized: we drop nulls and surface a note
in the caller instead of raising.
"""

from __future__ import annotations

from typing import Any

import httpx

from .. import settings
from ..cache import AsyncTTLCache, async_cached
from ..rate_limit import AsyncTokenBucket
from ..retry import upstream_retry


# --- GraphQL queries (verified live against openneuro.org/crn/graphql) ---

SEARCH_QUERY = """
query Search($q: DatasetSearchInput!, $first: Int) {
  advancedSearch(query: $q, first: $first) {
    edges {
      node {
        id
        publishDate
        latestSnapshot {
          tag
          description { Name }
          summary { modalities subjects tasks }
        }
      }
    }
  }
}
"""

DATASET_QUERY = """
query DS($id: ID!) {
  dataset(id: $id) {
    id
    name
    publishDate
    metadata {
      species
      associatedPaperDOI
      openneuroPaperDOI
      studyDomain
    }
    latestSnapshot {
      tag
      readme
      description {
        Name
        Authors
        DatasetDOI
        License
        Acknowledgements
        Funding
        ReferencesAndLinks
      }
      summary {
        modalities
        primaryModality
        subjects
        sessions
        tasks
        totalFiles
        size
      }
    }
  }
}
"""

FILES_QUERY = """
query Files($datasetId: ID!, $tag: String!) {
  snapshot(datasetId: $datasetId, tag: $tag) {
    id
    tag
    files {
      id
      filename
      size
      directory
      urls
    }
  }
}
"""

# Single-call recursive listing — returns every file in the snapshot, no per-
# directory follow-ups required. Verified live: returns 136 entries vs 23 for
# the non-recursive variant on ds000001. Use this whenever a modality filter
# is set; it's strictly faster than per-subject walks for any dataset >1 sub.
FILES_RECURSIVE_QUERY = """
query FilesRecursive($datasetId: ID!, $tag: String!) {
  snapshot(datasetId: $datasetId, tag: $tag) {
    files(recursive: true) {
      id
      filename
      size
      directory
      urls
    }
  }
}
"""

FILES_TREE_QUERY = """
query FilesTree($datasetId: ID!, $tag: String!, $tree: String!) {
  snapshot(datasetId: $datasetId, tag: $tag) {
    files(tree: $tree) {
      id
      filename
      size
      directory
      urls
    }
  }
}
"""


class OpenNeuroError(RuntimeError):
    """Raised for non-recoverable upstream errors after retries are exhausted."""


class OpenNeuroClient:
    def __init__(self) -> None:
        self._endpoint = settings.OPENNEURO_GRAPHQL_URL
        self._bucket = AsyncTokenBucket(rate_per_sec=settings.OPENNEURO_RATE_PER_SEC)
        self._cache = AsyncTTLCache(maxsize=512, ttl=settings.DEFAULT_CACHE_TTL)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.HTTP_CONNECT_TIMEOUT,
                read=settings.HTTP_READ_TIMEOUT,
                write=settings.HTTP_READ_TIMEOUT,
                pool=settings.HTTP_READ_TIMEOUT,
            ),
            headers={"User-Agent": "neuro-research-discovery-mcp/0.1.0"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- low-level POST ----
    async def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        await self._bucket.acquire()
        async for attempt in upstream_retry():
            with attempt:
                resp = await self._http.post(
                    self._endpoint,
                    json={"query": query, "variables": variables},
                )
                resp.raise_for_status()
                payload = resp.json()
        # `payload` is bound after the retry context exits successfully.
        if "errors" in payload and payload.get("data") is None:
            # Total failure (no data at all).
            raise OpenNeuroError(f"GraphQL errors: {payload['errors']}")
        return payload

    # ---- high-level operations ----

    async def search_datasets(
        self,
        keywords: str,
        modality: str | None,
        first: int,
    ) -> list[dict[str, Any]]:
        """Run advancedSearch. Returns the list of non-null nodes."""
        return await self._cache.get_or_set(
            f"search::{keywords}::{modality}::{first}",
            lambda: self._search_datasets_uncached(keywords, modality, first),
        )

    async def _search_datasets_uncached(
        self, keywords: str, modality: str | None, first: int
    ) -> list[dict[str, Any]]:
        q: dict[str, Any] = {"keywords": [keywords]}
        if modality:
            q["modality"] = modality.lower()
        payload = await self._post(SEARCH_QUERY, {"q": q, "first": first})
        edges = (payload.get("data") or {}).get("advancedSearch", {}).get("edges") or []
        # Filter null nodes (private/embargoed datasets that leak into the index).
        return [e["node"] for e in edges if e and e.get("node")]

    async def get_dataset(self, accession: str) -> dict[str, Any]:
        return await self._cache.get_or_set(
            f"dataset::{accession}",
            lambda: self._get_dataset_uncached(accession),
        )

    async def _get_dataset_uncached(self, accession: str) -> dict[str, Any]:
        payload = await self._post(DATASET_QUERY, {"id": accession})
        ds = (payload.get("data") or {}).get("dataset")
        if not ds:
            raise OpenNeuroError(f"Dataset not found: {accession}")
        return ds

    async def list_files(
        self, accession: str, tag: str, tree: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._cache.get_or_set(
            f"files::{accession}::{tag}::{tree or ''}",
            lambda: self._list_files_uncached(accession, tag, tree),
        )

    async def _list_files_uncached(
        self, accession: str, tag: str, tree: str | None
    ) -> list[dict[str, Any]]:
        if tree:
            payload = await self._post(
                FILES_TREE_QUERY,
                {"datasetId": accession, "tag": tag, "tree": tree},
            )
            snap = (payload.get("data") or {}).get("snapshot") or {}
            return snap.get("files") or []
        payload = await self._post(
            FILES_QUERY, {"datasetId": accession, "tag": tag}
        )
        snap = (payload.get("data") or {}).get("snapshot") or {}
        return snap.get("files") or []

    async def list_files_recursive(self, accession: str, tag: str) -> list[dict[str, Any]]:
        """Single-call recursive listing. Returns every file (no directories)."""
        return await self._cache.get_or_set(
            f"files_recursive::{accession}::{tag}",
            lambda: self._list_files_recursive_uncached(accession, tag),
        )

    async def _list_files_recursive_uncached(
        self, accession: str, tag: str
    ) -> list[dict[str, Any]]:
        payload = await self._post(
            FILES_RECURSIVE_QUERY, {"datasetId": accession, "tag": tag}
        )
        snap = (payload.get("data") or {}).get("snapshot") or {}
        return snap.get("files") or []
