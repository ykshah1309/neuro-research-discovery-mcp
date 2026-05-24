# v0.4 — Topic-Discovery Accuracy Milestone

This roadmap is locked in response to the v0.3.0 review verdict: *"DOI normalization
alone cannot break the research-accuracy ceiling. Make Crossref/OpenAlex/Semantic
Scholar an explicit v0.4 accuracy milestone, not a vague future wish."*

## Goal

Improve the **bridge tools'** ability to link results across sources beyond
exact-DOI match. Today a paper that uses an OpenNeuro dataset but isn't listed
on the dataset's metadata page is invisible to `find_papers_using_dataset`.
A NeuroVault collection whose paper was uploaded as a preprint then later
published with a different DOI escapes `find_neurovault_maps_for_paper`.

A research-grade discovery workflow needs to:
1. Resolve preprint ↔ published DOI pairs.
2. Find papers that *mention* a dataset accession in their full text.
3. Walk the citation graph to find papers that cite a given paper.
4. Reconcile PMID ↔ DOI ↔ PMC ID across all sources.

## Candidates (pick one first, spike for one week)

### Option A: Crossref (`api.crossref.org`)
**Pros:** Free, no auth, comprehensive (140 M works), polite-pool with email,
gives ISSN + venue + references. **Cons:** No full-text mining; reference list
is patchy on older works.
**Useful for:** preprint↔published linkage, reference enrichment, publication
metadata cleanup.

### Option B: OpenAlex (`api.openalex.org`)
**Pros:** Free, no auth, 250M works + a real citation graph, lifts most of
Microsoft Academic Graph's coverage, has explicit `concept` taxonomy.
**Cons:** Some hairy entity-disambiguation issues; rate limit 100k/day requires
email in User-Agent for "polite pool" (200k/day).
**Useful for:** *forward* citation search ("what papers cited this paper?"),
topic-graph search by `concept`, finding mentions in full text via SOLR.

### Option C: Semantic Scholar (`api.semanticscholar.org`)
**Pros:** Excellent NLP-derived citation contexts, "influential citations"
ranking, free 1 req/sec or 100 req/sec with API key.
**Cons:** Citation coverage skews CS/STEM-heavy.
**Useful for:** influence-weighted citation graph, citing-paper context strings.

## Recommendation

Start with **OpenAlex** (Option B). Reasons:
- Largest coverage by both works and citation edges, including biomedical.
- DOI / PMID / MAG ID cross-walk in a single record (kills the cross-ID
  reconciliation pain).
- The `concept` taxonomy gives us a server-side topic search that NeuroVault
  cannot do at all.
- "Polite pool" (just add email to User-Agent) is enough for our traffic.

## New tools to add in v0.4

| Tool | Family | What it does |
|---|---|---|
| `find_citing_papers_in_openalex(pmid_or_doi, max_results)` | new family E (enrichment) | Forward citation search via OpenAlex. |
| `enrich_paper(pmid_or_doi)` | E | Cross-walk PMID ↔ DOI ↔ PMC ID + venue + concepts. |
| `find_mentions_of_dataset(openneuro_accession)` | E | OpenAlex full-text search for the accession string (e.g. "ds000030"). |

These extend (don't replace) the existing 17 tools. The four bridge tools
get an opt-in `enrich: bool` flag that, when true, augments their results
with OpenAlex citation/concept data and upgrades `linkage_evidence` from
`keyword_match` → `cited_by`/`concept_match` where applicable.

## Non-goals for v0.4

- No new file-management or download tools (out of scope).
- No background graph crawling (single-call, single-hop only).
- No persistence of enrichment results beyond the per-request cache (no graph DB).

## Acceptance criteria

A canonical comprehensive search ("default mode network autism") should
return at least:
- 5 PubMed papers (current behavior)
- Their forward citations from OpenAlex (new)
- Any preprint DOIs that resolve to the same published work (new)
- Mentions of any OpenNeuro accession in those papers' full text (new)
- `linkage_evidence` with at least 3 distinct values across results

## Risk / cost

- OpenAlex polite-pool rate limit: 200k/day. Our token bucket + cache should
  keep us well under that.
- Extra latency: one HTTP round-trip per `enrich=true` bridge call.
- Coverage gaps: explicitly documented in tool descriptions, evidence labels.

**Time budget:** ~3 focused days. If the OpenAlex spike runs longer than that,
fall back to Crossref-only enrichment and re-scope.

## Out of scope, deferred to v0.5+

- Full-text retrieval (NCBI BioC, OpenAlex SOLR full-text).
- ML-based topic clustering across results.
- Persistent graph store.
- Crossref / Semantic Scholar parity (additive, not exclusive — pick after v0.4 ships).
- **Tool-response scanner / runtime policy layer.** OWASP MCP guidance
  increasingly flags tool-output prompt injection as a runtime problem, not
  just a schema problem. v0.3.x contains injection structurally (truncation
  + `UntrustedText` envelope + audit trail), but does not semantically detect
  injection-like payloads. A v0.5 spike should evaluate: (a) a small
  keyword/heuristic scanner that flags suspicious tokens like "ignore
  previous", `<system>`, "execute the following"; (b) optional LLM-based
  classification as a hook the MCP host can opt into. Both are advisory
  only — final defense is still at the host/agent layer.
