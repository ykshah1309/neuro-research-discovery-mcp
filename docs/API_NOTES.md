# API Notes

Per-source quirks discovered while probing each API live. Read this before
debugging surprising behavior — the upstream APIs disagree with their docs in
several places.

## OpenNeuro (GraphQL)

**Endpoint:** `POST https://openneuro.org/crn/graphql`. No auth required for public reads.

**Schema entry points we use:** `advancedSearch`, `dataset`, `snapshot`.

### Things that bit us

- **`search` is broken.** The top-level `search` field exists in the schema but always returns `null`. Use `advancedSearch` exclusively.
- **Modality is lowercase.** `"mri"`, `"eeg"`, `"meg"`, `"ieeg"`, `"pet"`, `"nirs"`. Uppercase `"MRI"` returns zero results silently — no error.
- **BIDS sub-modalities are *not* OpenNeuro modalities.** `"anat"`, `"func"`, `"dwi"` are subdirectories within a dataset, not values for the modality filter. Filter on them client-side via `summary.modalities` or the file listing.
- **Cursor field is occasionally null.** When `modality` is set, the server sometimes returns `null` for `edges[*].cursor` despite the schema declaring it non-nullable. This errors out the whole edge. We dropped `cursor` from our query because we don't paginate yet. If you add pagination, expect to handle this.
- **Partial-success responses are normal.** `advancedSearch` may include `edges[i].node = null` together with a top-level `errors` entry like `"You do not have access to read this dataset."` — private/embargoed datasets leak into the index. Filter null nodes; don't treat partial errors as fatal.
- **DOI provenance is scattered.** In order of usefulness:
  1. `latestSnapshot.description.DatasetDOI` — the dataset's own DOI, almost always populated.
  2. `metadata.associatedPaperDOI` — often empty string `""`, not `null`.
  3. `metadata.openneuroPaperDOI` — sometimes free text like `"To be released on Biorxiv"`. Don't assume DOI format.
  4. `latestSnapshot.description.ReferencesAndLinks` — free-text URL list.
- **File listing is non-recursive by default.** `snapshot.files` returns the top-level entries. Sub-directories appear as `directory: true, size: 0, urls: []`. To recurse, re-query with `snapshot(...) { files(tree: <id>) { ... } }` per directory.
- **`name` vs `description.Name`.** `dataset.name` is the uploader-supplied label; the BIDS-canonical title is `latestSnapshot.description.Name`. Prefer the latter.

## NeuroVault (REST)

**Base:** `https://neurovault.org/api/`. No auth required for read operations.

### Things that bit us

- **All query string filters are silently ignored.** `?search=`, `?DOI=`, `?DOI__iexact=`, `?modality=`, `?map_type=` — all return the unfiltered list with `count = 17333` (the total collection count). The only honored params are `limit` (capped at 500) and `offset`.
- **Consequence:** all keyword/DOI/modality filtering must be client-side. We build an in-memory collection index (~17k records, **~2–3 min** cold build with concurrency 8 — each page is ~1.5 MB / 7 s round-trip — 24 h TTL). The index is persisted to disk so restarts skip the build entirely (~100 ms load). See `disk_cache.py`.
- **DOI casing:** field is `DOI` (uppercase) on collections. There's also `preprint_DOI`. Both are often `null`. Compare case-insensitively and check both fields.
- **Sparse image IDs.** `/api/images/1/` → 404. Don't iterate IDs; iterate pages or descend from `collection.images`.
- **No publications endpoint.** `/api/publications/` 404s. Publication metadata lives directly on the collection object: `DOI`, `preprint_DOI`, `authors`, `paper_url`, `journal_name`.
- **Common nulls:** `analysis_level`, `smoothness_fwhm`, `cognitive_paradigm_cogatlas` — uploaders skip metadata frequently.
- **Pagination uses `next` (absolute URL).** Follow it rather than computing offsets; the API doesn't always agree with your math.
- **Rate limits:** none documented and none observed at moderate rates. We self-limit to 10 req/sec out of courtesy.

## PubMed (eutils via biopython)

**Library:** `Bio.Entrez`. **Base:** `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`.

### Things that bit us

- **`Entrez.read` requires a DOCTYPE declaration.** Real responses always include one (e.g. `<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch 20060628//EN" "https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/esearch.dtd">`). Test fixtures must include matching DOCTYPEs or parsing raises `ValueError`. The DTDs themselves are bundled with biopython (in `Bio/Entrez/DTDs/`).
- **`AbstractText` is a list.** Structured abstracts have multiple labeled segments (`Background:`, `Methods:`, `Results:`, `Conclusion:`). Join with newlines.
- **DOI lives in two places.** Check `Article.ELocationID` first (look for `EIdType="doi"`), fall back to `PubmedData.ArticleIdList` (look for `IdType="doi"`).
- **DOI → PMID:** use `esearch(term=f"{doi}[DOI]")`. The temptation to use `elink` is wrong — `elink dbfrom=pubmed_pmc` maps PubMed ↔ PMC, not DOI lookup. The NCBI ID Converter at `/pmc/utils/idconv/v1.0/` works too but isn't in biopython and has separate rate limits.
- **`Entrez.read` returns `StringElement` objects, not strings.** They have an `.attributes` dict. Use `str(x)` to get clean values.
- **`MeshHeadingList` is often empty for very recent articles** — they aren't indexed yet. Don't assume MeSH terms always exist.
- **Year may be missing from `JournalIssue.PubDate.Year`** — fall back to parsing the first 4 chars of `PubDate.MedlineDate`.
- **`Entrez` is blocking urllib.** Wrap every call with `asyncio.to_thread` in async code.
- **NCBI requires identification.** Set `Entrez.email`, `Entrez.tool`. Done at module import in `clients/pubmed.py` from `settings.PUBMED_EMAIL` and `settings.PUBMED_TOOL`.
- **Rate limits are server-side and per-IP (or per-API-key).** 3 req/sec anonymous, 10 req/sec with `Entrez.api_key`. Exceeding returns HTTP 429. We use a token bucket with the limit set at startup based on whether `PUBMED_API_KEY` is in env.
- **Batch `efetch` with comma-joined PMIDs in a single call.** NCBI accepts ~200 per request. This is much cheaper than N parallel calls.
- **`elink` related-articles first entry is the query PMID itself** — filter it out.

## Patterns we standardized on

- **Cache key** = sorted JSON of (op-name, args, kwargs) hashed to SHA-1 hex.
- **Rate-limit then retry then cache** — outer to inner. Cache hits skip the bucket entirely.
- **Field-extraction helpers** stay in the client module (not the tool module). The tool layer should not know about XML element shapes.
- **Errors are returned, not raised** at the MCP boundary. `errors.classify_exception` maps common cases (404, 429, 5xx, timeout, transport error, ValueError) to structured `ToolError` instances.
