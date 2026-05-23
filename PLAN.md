# neuro-research-discovery-mcp — Implementation Plan

> Locked in after live API probes against OpenNeuro, NeuroVault, and PubMed.
> The research findings change several defaults; this doc encodes them.

## Goal

Production-quality MCP server exposing 17 tools across three neuroimaging research data sources (OpenNeuro, NeuroVault, PubMed), with four cross-source "bridge" tools that connect them. Bridge tools are the differentiator — they let an agent move fluidly between dataset, derived map, and published paper.

## Architecture summary

- Async throughout (httpx async, asyncio.to_thread for the blocking biopython Entrez calls).
- Per-client token-bucket rate limiter (asyncio).
- Per-client tenacity retry decorator (exp backoff, retry on 5xx + connection errors, NOT 4xx).
- TTL cache (cachetools.TTLCache) wrapping all upstream calls; key = (method, sorted args). Default 1 h TTL.
- NeuroVault gets a long-TTL (24 h) collection-index cache because the API has no server-side search — see "NeuroVault constraint" below.
- Structured error model returned as JSON inside MCP TextContent rather than raised — never bubble raw exceptions to the client.

## Critical API findings (drive design)

### OpenNeuro (GraphQL)
- Endpoint: `POST https://openneuro.org/crn/graphql`, no auth.
- Use `advancedSearch` — `search` is broken (always returns null).
- Modality enum is lowercase: `"mri"`, `"eeg"`, `"meg"`, `"ieeg"`, `"pet"`, `"nirs"`. Sub-modalities like `anat`/`func`/`dwi` must be filtered client-side on `summary.modalities`.
- Partial-success is normal: `advancedSearch` may return `edges[i].node = null` with a top-level `errors` entry (private datasets leak through the index). Filter null nodes.
- Relay-style pagination — `endCursor` is opaque, pass through as `after`.
- DOI provenance, in order: `latestSnapshot.description.DatasetDOI` (almost always populated, this is the dataset's own DOI), `metadata.associatedPaperDOI` (often empty string, not null), `metadata.openneuroPaperDOI` (sometimes free text like "TBD"), `description.ReferencesAndLinks` (free-text).
- File listing is non-recursive; sub-directories require re-query with `files(tree: <id>)`. For our `list_openneuro_dataset_files`, list root by default and optionally recurse one level when `modality` filter is provided.

### NeuroVault (REST)
- **All query string filters are silently ignored.** `?search=`, `?DOI=`, `?modality=` — all return the full unfiltered list. The only honored params are `limit` (capped at 500) and `offset`.
- Forces a client-side index pattern. On first call to any search/filter operation, build a projection cache: `(id, name, description, DOI, preprint_DOI, authors, journal_name, number_of_images)` for all collections. 17,333 collections × 500/page = ~35 requests, with concurrency=4 ≈ 5 s first call. Cache TTL: 24 h.
- For image search: 663K images would be ~1.3K pages — too expensive. Strategy: keyword-search collections (via the index), then list images per matching collection via `/api/collections/{id}/images/`, then client-side filter on `map_type`/`modality`. Cap at N collections.
- No `/publications/` endpoint exists. Publication info lives on the collection object: `DOI`, `preprint_DOI`, `authors`, `paper_url`, `journal_name`.
- DOI field casing matters: `DOI` (uppercase). Compare case-insensitive on both `DOI` and `preprint_DOI`.

### PubMed (eutils via biopython)
- Entrez is sync `urllib`. Wrap every call with `asyncio.to_thread`.
- Set `Entrez.email`, `Entrez.tool = "neuro-research-discovery-mcp"`, optional `Entrez.api_key`.
- Rate limit: 3/s anonymous, 10/s with key. Use a token bucket; choose the limit at startup based on `os.environ.get("PUBMED_API_KEY")`.
- `AbstractText` is a list (structured abstracts have multiple labeled segments) — join with `\n`.
- DOI lives in two places: `Article.ELocationID` (with `EIdType=doi`) and `PubmedData.ArticleIdList`. Check both.
- DOI → PMID: `esearch(term=f"{doi}[DOI]")`. NOT elink.
- Batch efetch: comma-joined IDs in single request, up to ~200. Big perf win.

## File layout (locked)

```
neuro-research-discovery-mcp/
├── src/
│   ├── __init__.py
│   ├── server.py                  # MCP entry, tool registration
│   ├── models.py                  # all Pydantic schemas
│   ├── errors.py                  # ToolError model + classification
│   ├── cache.py                   # TTLCache helper + async lock
│   ├── retry.py                   # tenacity decorator factory
│   ├── rate_limit.py              # asyncio token bucket
│   ├── settings.py                # env loading (.env), constants
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── openneuro.py           # GraphQL client (raw httpx, no gql lib needed)
│   │   ├── neurovault.py          # REST + cached collection index
│   │   └── pubmed.py              # biopython Entrez + asyncio.to_thread
│   └── tools/
│       ├── __init__.py
│       ├── openneuro_tools.py     # 4 tools, Family A
│       ├── neurovault_tools.py    # 5 tools, Family B
│       ├── pubmed_tools.py        # 4 tools, Family C
│       └── bridge_tools.py        # 4 tools, Family D
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # httpx MockTransport fixtures, monkeypatched Entrez
│   ├── fixtures/                  # captured JSON/XML responses
│   ├── test_openneuro_client.py
│   ├── test_neurovault_client.py
│   ├── test_pubmed_client.py
│   ├── test_cache.py
│   ├── test_rate_limit.py
│   ├── test_models.py
│   ├── test_openneuro_tools.py
│   ├── test_neurovault_tools.py
│   ├── test_pubmed_tools.py
│   └── test_bridge_tools.py
├── docs/
│   ├── EXAMPLES.md
│   ├── ARCHITECTURE.md
│   └── API_NOTES.md
├── README.md
├── LICENSE
├── pyproject.toml
├── .env.example
└── .gitignore
```

### Note on `gql`
The spec lists `gql>=3.5.0` but for our four queries the dependency is overkill — raw httpx POST of the query string is simpler, has fewer transitive deps, and avoids gql's schema-validation overhead. We keep `gql` listed in pyproject for future-proofing but the client uses httpx directly. (Justification: every gql sync/async transport wraps httpx anyway; we lose nothing.)

## Tool inventory (17 total)

### Family A — OpenNeuro (4)
1. `search_openneuro_datasets(query, modality, max_results)` → `OpenNeuroSearchResult`
2. `get_openneuro_dataset(accession_number)` → `OpenNeuroDataset`
3. `list_openneuro_dataset_files(accession_number, modality=None)` → `OpenNeuroFileListing`
4. `get_openneuro_dataset_publications(accession_number)` → `OpenNeuroPublications`

### Family B — NeuroVault (5)
5. `search_neurovault_collections(query, max_results)` → `NeuroVaultCollectionSearchResult`
6. `search_neurovault_images(query, modality=None, map_type=None, max_results)` → `NeuroVaultImageSearchResult`
7. `get_neurovault_collection(collection_id)` → `NeuroVaultCollection`
8. `get_neurovault_image_metadata(image_id)` → `NeuroVaultImage`
9. `get_neurovault_collection_publications(collection_id)` → `NeuroVaultCollectionPublications`

### Family C — PubMed (4)
10. `search_pubmed(query, max_results, date_range_years=None)` → `PubMedSearchResult`
11. `get_pubmed_article(pmid)` → `PubMedArticle`
12. `get_pubmed_article_abstract(pmid)` → `PubMedAbstract`
13. `find_related_pubmed_articles(pmid, max_results)` → `PubMedRelatedResult`

### Family D — Bridge (4)
14. `find_papers_using_dataset(openneuro_accession)` — flow: get dataset → collect DOIs → for each DOI, `esearch term="<doi>[DOI]"` → fetch full PubMed records → return `CrossSourceResult` (pubmed_articles populated, openneuro_datasets has the source dataset).
15. `find_neurovault_maps_for_paper(pmid)` — flow: get pubmed article → extract DOI → search cached NeuroVault collection index by DOI / preprint_DOI → return `CrossSourceResult` (pubmed_articles=[src], neurovault_collections=matches).
16. `find_datasets_for_topic(research_topic, modality)` — flow: parallel `search_openneuro_datasets` + `search_neurovault_collections` → merge into `CrossSourceResult`.
17. `comprehensive_literature_search(research_question, modality=None)` — flow: PubMed search → fetch top N records → extract MeSH terms → use top MeSH + research_question to query OpenNeuro & NeuroVault in parallel → return unified `CrossSourceResult` with `suggested_next_queries` populated from MeSH terms.

## Pydantic model decisions

- All 17 tool inputs and outputs typed (Pydantic v2).
- `OpenNeuroDataset` includes `accession_number, title, description, modalities, num_subjects, num_sessions, tasks, species, download_url, associated_publications: list[str]` (DOIs) — matches spec.
- `NeuroVaultCollection`: include `paper_url` and `journal_name` in addition to spec fields, because they're cheap and useful for the bridge tools.
- `PubMedArticle`: `mesh_terms: list[str]` in addition to `keywords` (PubMed's `MeshHeadingList` is the real index; `keywords` is the author-supplied list).
- `CrossSourceResult`: as spec — query, three result lists, suggested_next_queries.
- `ToolError`: `error_type` (literal enum), `human_readable_message`, `upstream_status_code` (optional), `suggested_action`. Returned as JSON inside `TextContent` when a tool fails — never raise.

## Cross-cutting infrastructure

### `cache.py`
Wraps `cachetools.TTLCache` with an `asyncio.Lock` per-key to prevent thundering-herd on cold cache (multiple concurrent calls for the same key share one upstream request). Exposes `@async_cached(ttl_seconds=3600)` decorator.

### `rate_limit.py`
`AsyncTokenBucket(rate_per_sec, burst)` — `await bucket.acquire()` blocks until a token is available. Each client owns one bucket.

### `retry.py`
`retry_upstream()` factory returning `tenacity.retry(...)`-wrapped async decorator. Configured: 3 attempts, exp wait base=1s, max=30s, retry on `httpx.HTTPStatusError` with 5xx + `httpx.TransportError`. Do NOT retry on 4xx (raised + propagated as ToolError).

### `errors.py`
```python
class ToolError(BaseModel):
    error_type: Literal["rate_limited","not_found","api_unreachable","upstream_error","bad_input","timeout"]
    human_readable_message: str
    upstream_status_code: int | None = None
    suggested_action: str | None = None
```
`classify_exception(exc) -> ToolError` maps common exceptions to this shape.

## Testing strategy

### Unit (mocked) — CI candidate
- httpx.MockTransport with fixture JSON/XML files captured from real API hits during research.
- PubMed: monkeypatch `Bio.Entrez.{esearch,efetch,elink}` to return file-backed handles.
- Targets: every client method, every tool, error classification, cache behavior, rate limiter, retry trigger.

### Integration — opt-in via `@pytest.mark.integration`
- Real API calls; runs locally on demand (`pytest -m integration`). Not in CI by default.
- Smoke test per source: one search, one get-by-id, one bridge tool.

### Fixtures to capture
- `openneuro_search_autism.json` — advancedSearch with autism
- `openneuro_dataset_ds000001.json` — dataset query
- `openneuro_files_ds000001.json` — snapshot files
- `neurovault_collections_page1.json` — first page of /api/collections/?limit=500
- `neurovault_collection_457.json` — single collection (Stroop)
- `neurovault_collection_457_images.json` — images for collection
- `pubmed_esearch_dmn_autism.xml` — esearch response
- `pubmed_efetch_42134464.xml` — single article fetch
- `pubmed_elink_42134464.xml` — related articles

## Execution sequence (compressed)

Day 1 (today):
- Scaffold + infrastructure (cache/retry/rate_limit/errors)
- Pydantic models
- All three clients with mocked + 1 integration test each

Day 2:
- 13 per-source tools wired
- MCP server entry, install, smoke test through stdio

Day 3:
- 4 bridge tools (the hard ones)
- Polish, docs, push

For solo execution we'll compress this — but the order is locked.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| NeuroVault first-call latency (~30–60 s for index build) | Concurrent pagination (8 workers, ~7 s per 1.5 MB page), documented in README and tool descriptions. Could be persisted to disk later if needed. |
| OpenNeuro partial-success responses confuse tooling | Filter null nodes in client layer, never expose nulls to tool output |
| Entrez rate limit (3/s without key) breaks bridge tools that fan out | Bucket-throttle at the client; batch PubMed efetch with comma-joined PMIDs (≤200) |
| PubMed XML parsing is brittle (StringElement, structured abstracts) | All field extraction goes through one helper in pubmed.py with explicit fallbacks; tested with both flat and structured-abstract fixtures |
| Bridge tools fan out to many requests | Use `asyncio.gather` with bounded `Semaphore` per bridge call; cap fan-out (e.g., comprehensive_literature_search top-5 papers, not top-50) |
| pybids tries to import (not needed here) | Don't depend on pybids; openneuro client only queries metadata, no BIDS layout |
