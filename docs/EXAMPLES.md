# Example Agent Workflows

Worked examples showing how the tools chain together. Each example shows the agent's intent, the tool call(s) the LLM should make, and what comes back.

---

## 1. "Find recent papers on default mode network in autism, and any OpenNeuro datasets or NeuroVault maps associated with them."

One omnibus call:

```json
{
  "tool": "comprehensive_literature_search",
  "input": { "research_question": "default mode network autism" }
}
```

Returns a `CrossSourceResult` with:
- `pubmed_articles[]` — 5 most relevant articles with abstracts and MeSH terms.
- `openneuro_datasets[]` — datasets matched on the same query.
- `neurovault_collections[]` — collections matched on the same query.
- `suggested_next_queries[]` — follow-ups derived from the top papers' MeSH terms, e.g. *"Search OpenNeuro for datasets related to 'Brain'."*, *"Find NeuroVault maps for the top paper: find_neurovault_maps_for_paper(pmid='...')"*.

---

## 2. "I want to download the Human Connectome Project task fMRI data. What's available on OpenNeuro?"

Two calls:

```json
{ "tool": "search_openneuro_datasets", "input": { "query": "human connectome task fMRI", "modality": "mri", "max_results": 10 } }
```

Pick an accession from the result, then list the func/ files:

```json
{ "tool": "list_openneuro_dataset_files", "input": { "accession_number": "ds002785", "modality": "func" } }
```

---

## 3. "This paper (PMID: 12345678) — what brain maps did they publish on NeuroVault?"

```json
{ "tool": "find_neurovault_maps_for_paper", "input": { "pmid": "12345678" } }
```

The bridge tool fetches the PubMed record to extract the DOI, then scans the cached NeuroVault collection index for collections whose `DOI` or `preprint_DOI` matches. The returned `CrossSourceResult.neurovault_collections[]` is the answer; if it's empty, the `notes` field will say so.

---

## 4. "What datasets are available on OpenNeuro for diffusion imaging in healthy adults?"

```json
{ "tool": "search_openneuro_datasets", "input": { "query": "diffusion healthy adults", "modality": "mri", "max_results": 10 } }
```

OpenNeuro's `modality` filter is top-level (`"mri"`). To narrow further to DWI specifically, follow up with `list_openneuro_dataset_files(modality="dwi")` per candidate.

---

## 5. "Compare reproducibility studies of the n-back task. Find papers, datasets they used, and brain maps they published."

```json
{ "tool": "comprehensive_literature_search", "input": { "research_question": "n-back working memory reproducibility" } }
```

For each high-interest paper:

```json
{ "tool": "find_neurovault_maps_for_paper", "input": { "pmid": "<pmid>" } }
```

---

## 6. "I want to validate a new motion correction algorithm. What OpenNeuro datasets have multi-subject fMRI?"

```json
{ "tool": "search_openneuro_datasets", "input": { "query": "fMRI multi-subject motion", "modality": "mri", "max_results": 15 } }
```

Then for the top candidates:

```json
{ "tool": "get_openneuro_dataset", "input": { "accession_number": "<accession>" } }
```

Look at `num_subjects` and `tasks` to pick the most useful one. Combine with `nifti-inspector-mcp`'s `check_motion` once you've downloaded a BOLD run.

---

## 7. "What does NeuroVault have for the Stroop task that I can use as a benchmark?"

```json
{ "tool": "search_neurovault_collections", "input": { "query": "Stroop", "max_results": 10 } }
```

First ever call on a brand-new install takes ~2–3 min while the 17,000-collection index builds and persists to disk. After that, all subsequent calls (including across server restarts) are near-instant.

For each interesting collection:

```json
{ "tool": "get_neurovault_collection", "input": { "collection_id": 457 } }
```

---

## 8. "For OpenNeuro dataset ds000030, list every paper that has been published using it."

```json
{ "tool": "find_papers_using_dataset", "input": { "openneuro_accession": "ds000030" } }
```

Flow: get the dataset's metadata → collect DOIs from `DatasetDOI`, `associatedPaperDOI`, `openneuroPaperDOI` → for each DOI, resolve to a PMID via `esearch term="<doi>[DOI]"` → batch-fetch the full article records. The `notes` field reports how many DOIs were resolvable.

---

## 9. "Get the most-cited resting-state fMRI papers from the last 3 years."

```json
{ "tool": "search_pubmed", "input": { "query": "resting-state fMRI", "date_range_years": 3, "max_results": 20 } }
```

Note: NCBI doesn't expose citation counts via eutils; "most-cited" filtering happens client-side by sorting on the field if you can derive it (or by relevance, which PubMed already does).

---

## 10. "Show me the related articles to PMID 33000000."

```json
{ "tool": "find_related_pubmed_articles", "input": { "pmid": "33000000", "max_results": 10 } }
```

Uses NCBI's similarity index (`elink linkname=pubmed_pubmed`). Returns related PMIDs sorted by similarity score and full article records for each.

---

## Patterns to chain in agent prompts

- **Discover → drill down:** start with a search tool, pick a result, call `get_*` for full metadata.
- **Paper → maps:** `find_neurovault_maps_for_paper` after `search_pubmed`.
- **Dataset → papers:** `find_papers_using_dataset` after `search_openneuro_datasets`.
- **Topic → everything:** `comprehensive_literature_search` is the omnibus.
- **Cross with `nifti-inspector-mcp`:** download via `list_openneuro_dataset_files`, then inspect locally with `load_nifti` / `check_motion` from the sibling MCP.
