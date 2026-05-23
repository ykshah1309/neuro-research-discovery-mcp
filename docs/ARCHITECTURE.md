# Architecture

## Layered design

```
                   ┌──────────────────────────────┐
                   │      MCP stdio transport     │
                   │  (mcp.server.lowlevel)       │
                   └──────────────┬───────────────┘
                                  │
                   ┌──────────────▼───────────────┐
                   │       server.py              │
                   │  - tool registration         │
                   │  - dispatch + error mapping  │
                   └──────────────┬───────────────┘
                                  │ Pydantic in/out models
                   ┌──────────────▼───────────────┐
                   │  tools/{openneuro,neurovault,│
                   │  pubmed,bridge}_tools.py     │
                   │  - Pydantic <-> client dicts │
                   └──────┬────────────┬──────────┘
                          │            │
         ┌────────────────▼───┐   ┌────▼──────────────┐
         │ clients/openneuro  │   │ clients/{nv,pm}   │
         │ clients/neurovault │   │ + per-client      │
         │ clients/pubmed     │   │   cache, retry,   │
         └────────┬───────────┘   │   rate limit      │
                  │ httpx /        └────────┬──────────┘
                  │ asyncio.to_thread       │
                  ▼                          ▼
         OpenNeuro GraphQL          PubMed eutils / NeuroVault REST
```

Three layers: **transport** (MCP), **tools** (Pydantic contract), **clients** (HTTP / Entrez). Each tool function takes a Pydantic input model + a client instance and returns a Pydantic output model. The server layer is dumb: it pattern-matches the tool name to a tool function and serializes the result.

## Async everywhere — and one place it's actually sync

httpx is async-native and so is the MCP server. The only sync API in the stack is biopython's `Bio.Entrez`. We don't try to async-wrap urllib by hand; we just call `asyncio.to_thread` around the blocking `Entrez.{esearch,efetch,elink} + Entrez.read` work. The token bucket gates entry into the thread pool so concurrency stays bounded.

## Cache, retry, rate limit — three independent concerns

- `cache.py` — `AsyncTTLCache` wraps `cachetools.TTLCache` and adds a per-key `asyncio.Lock`. The lock collapses 10 concurrent cold-misses into one upstream call. Test: `test_cache_collapses_concurrent_misses_to_one_factory_call`.
- `rate_limit.py` — token bucket. Each client owns one bucket. PubMed's bucket size depends on whether `PUBMED_API_KEY` is set (`settings.PUBMED_RATE_PER_SEC = 9.0 or 2.5`). Tests: `test_rate_limit.py`.
- `retry.py` — tenacity decorator. 3 attempts, exp backoff 1-30 s. Retries only on 5xx + httpx.TransportError; 4xx is propagated as `bad_input` / `not_found` via `errors.classify_exception`.

Stacking order inside each client method: rate limit → retry → cache. That is, `acquire token → retry-wrapped HTTP call → store in cache`. Cache hits skip rate limiting entirely.

## NeuroVault is special

NeuroVault's REST API silently ignores every querystring filter — `?search=`, `?DOI=`, `?modality=`, all of them. The only honored params are `limit` and `offset`. This is documented in `docs/API_NOTES.md`. The practical consequence: we maintain a **collection index** in memory.

The index is a projection (id, name, description, DOI, preprint_DOI, authors, journal_name, number_of_images, paper_url, download_url) for all ~17,000 collections. It's built once by concurrent pagination (8 workers, 500/page), then cached in memory **and persisted to disk** at `~/.cache/neuro-research-discovery-mcp/neurovault_index.json` (or `%LOCALAPPDATA%\neuro-research-discovery-mcp\` on Windows). On a truly cold cache (no disk file) the first call takes ~2–3 min (each page is ~1.5 MB / 7 s end-to-end; we observed 168 s in production). Subsequent server restarts load from disk in ~100 ms. After the in-memory TTL expires, stale-while-revalidate kicks in: callers get instant stale results while a background refresh runs.

All keyword and DOI lookups query the index in-memory. Image search restricts to images of collections that match the keyword (capped at 10 collections to bound fan-out), then filters by `modality` and `map_type` client-side.

## OpenNeuro quirks

- Use `advancedSearch`, not `search` (the latter always returns null).
- Modality values are lowercase: `"mri"`, `"eeg"`, etc. BIDS sub-modalities (anat/func/dwi) don't filter at this layer; they live in `summary.modalities` and must be filtered client-side.
- The server occasionally returns null edge cursors when filters are combined (an upstream bug). We dropped the `cursor` field from our query — we don't paginate yet, so it's not load-bearing.
- DOIs are spread across three fields: `latestSnapshot.description.DatasetDOI` (the dataset's own DOI, almost always set), `metadata.associatedPaperDOI` (often empty string, not null), `metadata.openneuroPaperDOI` (sometimes free text like "TBD"). We collect from all three and filter out obvious non-DOI values.

## PubMed quirks

- `Entrez.read` requires a DOCTYPE declaration in the XML or it raises ValueError. Real NCBI responses always include one; mocked test responses must too. See `tests/conftest.py`'s `_EFETCH_DOCTYPE` constant.
- `AbstractText` is a list (structured abstracts have multiple labeled segments — "Background:", "Methods:", etc.). We join with `\n`.
- DOI lives in two places: `Article.ELocationID` and `PubmedData.ArticleIdList`. We check both with a fallback.
- DOI → PMID: use `esearch(term=f"{doi}[DOI]")`. **Do not** use elink — it maps PubMed ↔ PMC, not DOI lookup.
- Batch `efetch` with comma-joined PMIDs in a single call. NCBI accepts ~200 per request. This is the biggest single perf win on the bridge tools.

## Bridge tools — fan-out budget

The bridge tools chain multiple upstream calls. To keep per-call cost predictable:

- `find_papers_using_dataset` — bounded by N DOIs returned by OpenNeuro for the dataset (typically 1–3).
- `find_neurovault_maps_for_paper` — single PubMed call + single NeuroVault index lookup.
- `find_datasets_for_topic` — parallel: 1 OpenNeuro search + 1 NeuroVault search (against index, sub-ms after warmup).
- `comprehensive_literature_search` — PubMed search (≤ 5 articles) → parallel OpenNeuro + NeuroVault search. MeSH terms from top 5 papers feed `suggested_next_queries`. Worst case: 3 upstream calls + ≤200-PMID batch fetch.

DOI → PMID resolution inside `find_papers_using_dataset` uses `asyncio.Semaphore(4)` to bound concurrent Entrez calls.

## Error handling philosophy

Tools never raise. Every exception is caught in `server._call_tool` and run through `errors.classify_exception`. The result is a Pydantic `ToolError` with:

- `error_type`: one of `rate_limited`, `not_found`, `api_unreachable`, `upstream_error`, `bad_input`, `timeout`, `internal_error`.
- `human_readable_message`: text the model can show to the user verbatim.
- `upstream_status_code`: optional HTTP status if applicable.
- `suggested_action`: short remediation hint ("Retry in 60 seconds", "Set PUBMED_API_KEY for higher limits", etc.).

The client (Claude Desktop) receives JSON inside a TextContent — same shape as success — so the LLM can react gracefully.

## Why not `gql`?

The spec lists `gql>=3.5.0` and we install it for compatibility, but the OpenNeuro client uses raw `httpx.AsyncClient.post` instead. Reasons:

1. `gql` adds graphql-core, multidict, propcache, yarl, backoff — none needed for four straightforward queries.
2. Every gql async transport wraps httpx anyway.
3. We don't want gql's schema-validation overhead (we'd need to re-introspect the schema, and OpenNeuro's schema includes invalid sub-states like nullable cursors that gql refuses).

If we ever add subscription support or schema-validated mutations, gql becomes attractive again. For now: raw httpx.

## What's *not* here

- No persistence layer. The cache is in-memory and dies with the process.
- No CLI tool. Just the MCP server.
- No web UI.
- No authentication. None of the upstream APIs require it for the read operations exposed.
- No OAuth flow scaffolding.
- No data-derived analyses (no map comparison, no statistics — that's `nifti-inspector-mcp`'s territory).

If any of those become necessary later, this layout has room (cache → swap for Redis; add a `derived/` tool family). But YAGNI for now.
