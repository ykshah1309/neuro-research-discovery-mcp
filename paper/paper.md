---
title: 'neuro-research-discovery-mcp: A Model Context Protocol Server for Cross-Source Neuroimaging Literature and Dataset Discovery'
tags:
  - Python
  - Model Context Protocol
  - neuroimaging
  - reproducibility
  - large language models
  - scientific discovery
authors:
  - name: Yash Kamlesh Shah
    orcid: 0009-0003-8041-7649
    affiliation: 1
affiliations:
  - name: Ying Wu College of Computing, New Jersey Institute of Technology, USA
    index: 1
date: 30 June 2026
bibliography: paper.bib
---

# Summary

`neuro-research-discovery-mcp` is a Python server that exposes nineteen typed
tools for neuroimaging research discovery through the Model Context Protocol
(MCP) [@anthropic2024mcp], a recent standard for connecting large language
models to external data sources. The server bridges three canonical
repositories: OpenNeuro for BIDS-organized raw datasets, NeuroVault for
unthresholded statistical maps and the collections that publish them, and
PubMed for the biomedical literature that cites both. Four cross-source
"bridge" tools resolve relationships across the repositories by normalized
DOI, so an AI assistant can answer compound questions in a single call:
*which PubMed articles use OpenNeuro dataset `ds000030`*, *which NeuroVault
collections were published alongside PubMed article `PMID:26178017`*, or
*what is the current literature, raw data, and statistical-map landscape
for "default mode network in autism"*. The intended users are neuroimaging
researchers who use AI assistants for discovery and triage tasks that
currently require manual cross-walking between three different web
interfaces and three different identifier systems. The software is
licensed MIT and requires Python ≥3.11.

# Statement of Need

Reproducibility in human-subject neuroimaging research depends on capturing
the full analytic chain — not only the statistical step that produces a
contrast map, but every preceding decision that selected the dataset,
chose the comparison literature, and surfaced the prior statistical maps
under consideration. The Neuroimaging Analysis Replication and Prediction
Study (NARPS) demonstrated how fragile that chain is: seventy independent
analysis teams given the same fMRI dataset and the same nine hypotheses
chose no two identical workflows, and per-hypothesis reporting rates of
significance ranged from 5.7% to 84.3% [@botvinikNezer2020variability].
The community response has focused on standardizing the analytic step.
The discovery step — *which dataset, which prior maps, which literature*
— remains largely informal, executed through ad-hoc browser sessions, and
almost never logged.

The neuroimaging data ecosystem is fragmented across three canonical
machine-readable sources, none of which interoperate at the query layer.
The Brain Imaging Data Structure [@gorgolewski2016bids] established
file-level interoperability for raw datasets, and OpenNeuro made BIDS
datasets globally accessible. NeuroVault [@gorgolewski2015neurovault]
plays an analogous role for unthresholded statistical maps. PubMed
indexes the literature that cites both. Each source publishes its own
API, identifier system, and DOI conventions. A researcher who wants to
"find papers using dataset X and the brain maps they published" must
manually reconcile OpenNeuro accession numbers, NeuroVault collection
identifiers, PubMed PMIDs, and three inconsistent DOI representations.
No equivalent of BIDS exists at the cross-source query layer.

Recent work on agentic large language models has established the
reason–act tool-use loop as a general pattern [@yao2023react], and the
Model Context Protocol [@anthropic2024mcp] standardizes the transport
through which such agents interact with external systems. MCP servers
have begun to appear for adjacent biomedical domains: MCPmed
[@flotho2025mcpmed] proposes an MCP-enabled interface to GEO, STRING,
and the UCSC Cell Browser for bioinformatics discovery, and EHR-MCP
[@masayoshi2025ehrmcp] demonstrates real-world clinical information
retrieval at Keio University Hospital. Both validate provenance-preserving
MCP servers in scientific settings; neither targets the
OpenNeuro–NeuroVault–PubMed triple. The software described here is, to
our knowledge, the first MCP server to bridge these three neuroimaging
repositories with provenance-preserving cross-walks.

This software contributes three things: (i) a single MCP typed-tool
interface to all three sources, with input and output schemas derived
from Pydantic models so an agent receives validated data rather than
parsed HTML; (ii) DOI-based cross-source bridge tools that resolve
relationships across the repositories; and (iii) per-call audit logging
structured as W3C PROV-O activity records [@w3c2013provo] so an
AI-assisted discovery session can be replayed and verified after the fact.

# Software Description

## Architecture overview

The server is implemented in Python (≥3.11) and built on the official
`mcp` Python SDK version 1.27.1. It uses the low-level
`mcp.server.lowlevel.Server` API rather than the higher-level FastMCP
shim; this is deliberate, because in `mcp` SDK 1.27.1 the low-level
API is the only path that exposes the `outputSchema`,
`structuredContent`, and `annotations` fields required for
spec-compliant typed tools. The
primary transport is stdio, suitable for Claude Desktop and other
MCP-compatible clients. An optional FastAPI HTTP transport ships as a
`web` extra, exposing the same nineteen tools through plain JSON
endpoints with a separate Server-Sent Events stream for the live audit
log. All 38 production dependencies are version-pinned in
`constraints.txt`, with a stricter `constraints-dev.txt` for development
and continuous integration. Every tool declares both `inputSchema` and
`outputSchema` (Pydantic v2 JSON Schema), annotates the four MCP behavior
hints (`readOnlyHint=true`, `openWorldHint=true`, `idempotentHint=true`,
`destructiveHint=false`), and returns a structured `ToolError` with
`isError=true` on failure. The `readOnlyHint=true` declaration is
load-bearing: it permits the audit log described below to be treated as
a complete record of the session's effect on the world. Quality control
follows the same discipline as the protocol surface: the suite ships
112 unit tests with 74% line coverage measured by `coverage.py`,
including a doc-drift suite (`tests/test_doc_drift.py`) that asserts the
README's headline tool count, tool-name enumeration, and security-section
field list stay in lockstep with the live server's `_list_tools()`
output.

## Family A — OpenNeuro

Four tools wrap the OpenNeuro GraphQL API and surface BIDS-organized
dataset metadata: `search_openneuro_datasets`, `get_openneuro_dataset`,
`list_openneuro_dataset_files`, and `get_openneuro_dataset_publications`.
Together they answer dataset-discovery queries (find datasets matching a
keyword and modality), per-dataset detail queries (subjects, sessions,
tasks, modalities, species), file listings within a dataset's latest
snapshot, and the set of paper DOIs that the dataset's metadata
associates with prior publications.

## Family B — NeuroVault

Seven tools wrap NeuroVault's REST API:
`search_neurovault_collections`, `search_neurovault_images`,
`get_neurovault_collection`, `get_neurovault_image_metadata`,
`get_neurovault_collection_publications`, `get_neurovault_cache_status`,
and `prewarm_neurovault_index`. The architecture of this family is
shaped by a discovery during implementation: the NeuroVault REST API
silently ignores every server-side filter. `?search=`, `?DOI=`,
`?modality=`, and every other querystring filter we tested returned
HTTP 200 with a valid response and `count=17333`, the total number of
collections in NeuroVault, regardless of input. Only `limit` and
`offset` are honored. The server consequently maintains a locally
persisted collection index of all 17,333 records (10 projected fields
per collection, 6.18 MB JSON on disk), built by concurrent pagination
over the catalog in approximately 168 s on first run and loaded in
approximately 80 ms on subsequent runs (median of 5 trials, 62–94 ms
range). The index file is schema-versioned (`SCHEMA_VERSION=2`),
size-capped (20 MB), validated on load, and held to a 24-hour
time-to-live with a stale-while-revalidate window of 2× TTL.

## Family C — PubMed

Four tools wrap NCBI's E-utilities (`esearch`, `efetch`, `elink`):
`search_pubmed`, `get_pubmed_article`, `get_pubmed_article_abstract`,
and `find_related_pubmed_articles`. NCBI's usage policy requires the
caller to identify a contact email; this is enforced at server startup
via the `PUBMED_EMAIL` environment variable and an optional
`NEURO_REQUIRE_PUBMED_EMAIL` flag that refuses to start with a
placeholder address. All PubMed XML parsing is centralized in one
helper, which handles three non-obvious cases easy to miss when
implementing against E-utilities directly: structured abstracts return
`AbstractText` as a list of labeled segments; DOIs may appear in either
`Article.ELocationID` or `PubmedData.ArticleIdList`; and DOI-to-PMID
lookup must use `esearch term="<doi>[DOI]"`, not the apparently obvious
`elink dbfrom=pubmed_pmc`, which maps PubMed to PubMed Central.

## Family D — Bridge / cross-source

Four bridge tools resolve relationships across the three repositories:
`find_papers_using_dataset`, `find_neurovault_maps_for_paper`,
`find_datasets_for_topic`, and `comprehensive_literature_search`. Each
links results across sources by normalized DOI. DOI normalization is
itself a small dedicated module (`doi.py`): strip URL prefixes
(`https://doi.org/`, `http://dx.doi.org/`), strip a leading `doi:`,
lowercase the suffix, validate against the regular expression
`^10\.\d{4,9}/\S+$`. Before this
normalization layer was added, `find_neurovault_maps_for_paper` missed
approximately 40% of expected matches to casing and prefix
inconsistencies alone. Each cross-source result carries a
`linkage_evidence` label distinguishing `doi_exact` (a confirmed DOI
match across sources) from `keyword_match` (a topical co-occurrence
that should be treated as a lead, not as evidence of citation). The
keyword ceiling is the explicit target of a planned v0.4 release, which
will add OpenAlex enrichment for forward-citation linkage.

## Audit logging

Every tool call emits a single JSON object to standard error via the
`neuro_research_discovery.audit` logger. The schema is intentionally
small: `ts` (call start, Unix seconds), `tool` (the invoked tool name),
`arg_keys` (sorted argument *keys only* — values are never logged,
because free-text queries can be sensitive), `elapsed_ms` (wall-clock
latency), `is_error` and `error_type`, and `cache_hits` / `cache_misses`
counters from the in-memory TTL cache. A real-call example appears in
the usage section. Each line maps cleanly onto W3C PROV-O concepts
[@w3c2013provo]: the MCP client is the `Agent`, the tool invocation is
the `Activity`, and the structured response is the generated `Entity`.
The discipline of separating monotonic and wall-clock time in this code
is itself a regression test. An earlier unit-mismatch bug stored
`time.time()` in a field intended for `time.monotonic()`. The resulting
–1.78-billion-second cache age silently passed every time-to-live check
until the test `_index_age() >= 0` after disk load was added.

# Usage Example

## Framing

Consider a representative compound research query: *a researcher wants
to find recent papers on default mode network in autism, plus any
associated OpenNeuro datasets and NeuroVault statistical maps for the
same topic*. This question touches all three sources and requires
cross-walking between PubMed paper DOIs and NeuroVault collection DOIs.
With this MCP installed, the researcher (or the AI assistant acting on
their behalf) issues a single tool call against
`comprehensive_literature_search`. The configuration and call are shown
below; the structured response is described after the code blocks.

## Configuration

The package installs from source with `pip install -e .` (or directly
from the public repository via `pip install git+https://github.com/ykshah1309/neuro-research-discovery-mcp.git`).
Once installed, the server is registered with Claude Desktop (or any
MCP-compatible client) through the standard JSON configuration:

```json
{
  "mcpServers": {
    "neuro-research-discovery": {
      "command": "neuro-research-discovery",
      "env": {
        "PUBMED_EMAIL": "your.real.email@institution.edu",
        "NEURO_REQUIRE_PUBMED_EMAIL": "1"
      }
    }
  }
}
```

## The single tool invocation

A deployed MCP client (Claude Desktop, an OpenAI agent, an in-house
runner) issues a JSON-RPC `tools/call` request over the configured
transport. For brevity we show the equivalent Python form against the
server's internal dispatch surface, which produces the same
`CallToolResult`:

```python
await server._call_tool(
    "comprehensive_literature_search",
    {"research_question": "default mode network in autism"},
)
```

## What the agent receives

The structured response returns five PubMed articles, six OpenNeuro
datasets matching the topic, and zero NeuroVault collections. The
zero here is falsifiable: the comprehensive tool internally invokes
the same DOI cross-walk used by `find_neurovault_maps_for_paper` for
each of the top five PubMed papers, and the empty
`neurovault_collections` list together with the absence of any
`doi_exact` entries in `linkage_evidence` reflects zero matches across
all five lookups against the 17,333-collection index — a structural
property of the corpus rather than a tool failure. Each article carries title, authors, journal, year, abstract,
DOI, and MeSH terms. Every upstream-supplied text field is wrapped in
an `UntrustedText` Pydantic model with `source`, `truncated`,
`original_length`, and a literal `trust: "untrusted_upstream"` marker,
and the response includes a top-level `untrusted_text_warning`
advisory string.
The response also includes four `suggested_next_queries` representing
concrete bridge-tool follow-ups the client may auto-invoke, and an
eleven-entry `linkage_evidence` dictionary in which every entry for
this query is labeled `keyword_match`. The total wall-clock time from
`_call_tool` entry to return was 2.69 s on a warm NeuroVault disk
cache. The absence of `doi_exact` links here is the keyword-ceiling
phenomenon discussed earlier; raising it is the goal of the planned
OpenAlex enrichment.

## The audit-log line emitted

```json
{"ts": 1779814170.074, "tool": "comprehensive_literature_search", "arg_keys": ["research_question"], "elapsed_ms": 2687.0, "is_error": false, "error_type": null, "cache_hits": 0, "cache_misses": 3}
```

# Comparison to Existing Work

## MCP application papers in adjacent biomedical domains

The closest precedents are MCPmed [@flotho2025mcpmed], which proposes
MCP servers for bioinformatics resources including GEO, STRING, and the
UCSC Cell Browser, and EHR-MCP [@masayoshi2025ehrmcp], which validates
MCP-mediated retrieval against an operational clinical information
system at Keio University Hospital. Both establish the viability of
provenance-preserving MCP servers in scientific workflows, and both are
out of scope for neuroimaging cross-source discovery: MCPmed targets
molecular-biology resources, and EHR-MCP targets clinical records under
hospital governance. Neither addresses the OpenNeuro–NeuroVault–PubMed
triple or DOI-based cross-walks between published statistical maps, raw
datasets, and the literature that cites them.

## Methodological context from MCP audits

A recent audit of 91 vision-centric MCP servers [@tiwari2025mcpvision]
reported 78.0% schema misalignment and 89.0% untyped tool connections
across the surveyed ecosystem; an ecosystem-wide study of 1,899 servers
[@hasan2025mcpglance] documented similar quality and security gaps. The
software described here was designed to address these failure classes
directly: every tool declares typed input and output schemas in Pydantic
v2, every tool annotates the four behavior hints, and every error
returns a structured `ToolError` with `isError=true` rather than
propagating an exception. The audit log gives a deployment a record
sufficient to verify each of these claims after the fact.

## Existing neuroimaging tooling

Existing neuroimaging tooling includes BIDS validators
[@gorgolewski2016bids] and preprocessing pipelines such as fMRIPrep, as
well as direct Python clients for each repository (`datalad` for
DataLad-backed OpenNeuro datasets, custom REST clients for NeuroVault,
`Bio.Entrez` for PubMed). Each is task-specific and was not designed
for AI-agent consumption: these tools expose no typed schemas, do not
speak MCP, and produce no audit-friendly record of cross-source
queries. A sibling project, `nifti-inspector-mcp`, complements this
server by providing local NIfTI and BIDS file inspection over the same
MCP transport; together they support a discovery-to-inspection workflow
within a single AI-assistant conversation.

# Acknowledgments

The author thanks **[ADVISOR NAME]** at the Ying Wu College of
Computing, New Jersey Institute of Technology, for guidance on the
research scope and on framing the contribution for the neuroimaging
methods community. **[LAB MEMBERS / EARLY TESTERS]** provided feedback
on early prototypes of the bridge tools and helped surface several of
the upstream-API edge cases documented in the software description.
The author also thanks the maintainers of OpenNeuro, NeuroVault, and
NCBI E-utilities for operating the open data and literature
infrastructure that this software depends on. No external funding
supported this work.

# References
