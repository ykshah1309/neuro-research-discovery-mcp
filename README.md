# neuro-research-discovery-mcp

MCP server bridging **OpenNeuro**, **NeuroVault**, and **PubMed** for AI-agent-driven neuroimaging research discovery.

## What this is

Three of the most useful neuroimaging research data sources — raw BIDS datasets (OpenNeuro), derived statistical maps (NeuroVault), and the published literature (PubMed) — live in three different APIs with three different query languages and zero cross-references. An agent that wants to answer "what datasets and brain maps exist for the papers most cited on default-mode-network autism research?" today has to chain three searches manually.

This MCP exposes **17 typed tools** (4 + 5 + 4 + 4) across those three sources, plus four **bridge tools** that cross-walk between them via DOI matching and parallel keyword search. The bridge tools are the differentiator: they're what makes this more than three separate API wrappers.

Built as a sibling to [nifti-inspector-mcp](https://github.com/ykshah1309/nifti-inspector-mcp), which handles local NIfTI / BIDS file inspection.

## Installation

```bash
git clone https://github.com/ykshah1309/neuro-research-discovery-mcp.git
cd neuro-research-discovery-mcp
pip install -e .
cp .env.example .env   # optional — only needed to lift PubMed rate limit
```

Requires Python 3.11+. No NCBI account is required for basic PubMed usage (anonymous mode is rate-limited to 3 req/sec); set `PUBMED_API_KEY` in `.env` to lift the limit to 10 req/sec.

## Claude Desktop configuration

Add to `claude_desktop_config.json`:

- macOS / Linux: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "neuro-research-discovery": {
      "command": "neuro-research-discovery"
    }
  }
}
```

If the `neuro-research-discovery` console script isn't on PATH for Claude Desktop (PATH inheritance differs between shells and GUI apps on Windows), use:

```json
{
  "mcpServers": {
    "neuro-research-discovery": {
      "command": "python",
      "args": ["-m", "neuro_research_discovery.server"]
    }
  }
}
```

Restart Claude Desktop fully (system-tray Quit, then relaunch). The 17 tools will appear in the tools picker.

## Available tools

### Family A — OpenNeuro (BIDS datasets)
| Tool | What it does |
|---|---|
| `search_openneuro_datasets` | Keyword + optional modality search; returns dataset summaries |
| `get_openneuro_dataset` | Full metadata for one dataset by accession (e.g. `ds000001`) |
| `list_openneuro_dataset_files` | List files in the latest snapshot; optional modality filter (anat/func/dwi) |
| `get_openneuro_dataset_publications` | Dataset DOI + associated paper DOIs + reference links |

### Family B — NeuroVault (statistical maps)
| Tool | What it does |
|---|---|
| `search_neurovault_collections` | Keyword search over name/description/authors/journal |
| `search_neurovault_images` | Search images via parent collections, filter by modality / map_type |
| `get_neurovault_collection` | Get one collection by integer ID |
| `get_neurovault_image_metadata` | Get one image's metadata by integer ID |
| `get_neurovault_collection_publications` | DOI + paper URL + journal info for a collection |

### Family C — PubMed (literature)
| Tool | What it does |
|---|---|
| `search_pubmed` | Query PubMed; optional date_range_years; returns full article records |
| `get_pubmed_article` | Full article (title/authors/journal/abstract/DOI/MeSH) by PMID |
| `get_pubmed_article_abstract` | Just the title + abstract by PMID (lightweight) |
| `find_related_pubmed_articles` | Use NCBI's similarity index to find related articles |

### Family D — Bridge tools (cross-source)
| Tool | What it does |
|---|---|
| `find_papers_using_dataset` | OpenNeuro accession → DOIs → PubMed records that cite the dataset |
| `find_neurovault_maps_for_paper` | PMID → DOI → NeuroVault collections that link back to the paper |
| `find_datasets_for_topic` | Parallel keyword search across OpenNeuro + NeuroVault |
| `comprehensive_literature_search` | PubMed search → extract MeSH terms → search all three sources |

See [`docs/EXAMPLES.md`](docs/EXAMPLES.md) for realistic agent workflows using these.

## Example queries

These run live in Claude Desktop once the server is configured.

1. **"Find recent papers on default mode network in autism, and any OpenNeuro datasets or NeuroVault maps associated with them."**
   → `comprehensive_literature_search(research_question="default mode network autism")`

2. **"What's available on OpenNeuro for diffusion imaging in healthy adults?"**
   → `search_openneuro_datasets(query="diffusion healthy adults", modality="mri")`

3. **"This paper (PMID: 12345678) — what brain maps did they publish on NeuroVault?"**
   → `find_neurovault_maps_for_paper(pmid="12345678")`

4. **"What does NeuroVault have for the Stroop task that I can use as a benchmark?"**
   → `search_neurovault_collections(query="Stroop")`

5. **"For OpenNeuro dataset ds000030, list every paper that has been published using it."**
   → `find_papers_using_dataset(openneuro_accession="ds000030")`

6. **"Compare reproducibility studies of the n-back task. Find papers, datasets they used, and brain maps they published."**
   → `comprehensive_literature_search(research_question="n-back working memory reproducibility")`

7. **"Get the most-cited papers on resting-state fMRI from the last 3 years, then find related datasets."**
   → `search_pubmed(query="resting-state fMRI", date_range_years=3)` → `find_datasets_for_topic(research_topic="resting-state fMRI")`

8. **"List the func/ files for OpenNeuro ds000001 so I know what BOLD runs are available."**
   → `list_openneuro_dataset_files(accession_number="ds000001", modality="func")`

More worked examples in [`docs/EXAMPLES.md`](docs/EXAMPLES.md).

## Architecture

- **Async everywhere** — httpx async client for OpenNeuro and NeuroVault; biopython Entrez (sync) is wrapped in `asyncio.to_thread`.
- **Per-client rate limiting** — token bucket: 10 req/sec on OpenNeuro / NeuroVault, 3 req/sec on PubMed (lifted to 9 req/sec with `PUBMED_API_KEY`).
- **Tenacity retries** — 3 attempts, exp backoff (1–30 s), only on 5xx + transport errors. 4xx propagates as structured ToolError.
- **TTL cache** — `cachetools.TTLCache` wrapped with per-key asyncio lock to collapse thundering-herd misses to one upstream call. Default 1 h TTL.
- **NeuroVault collection index** — NeuroVault has zero server-side search; we paginate the full collection list once (first ever run ~2–3 min, restarts instant via disk cache), project to a small dict, and cache for 24 h. All keyword and DOI lookups query the index in-memory.
- **Structured errors** — exceptions never propagate to the MCP client. They're classified into a `ToolError` Pydantic model with `error_type`, human message, upstream status code, and suggested action.

Full design rationale in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Per-source quirks documented in [`docs/API_NOTES.md`](docs/API_NOTES.md).

## Running the tests

```bash
pip install -e ".[dev]"
pytest tests/                       # 22 unit tests, mocked HTTP, < 5s
pytest tests/ -m integration        # opt-in live API hits (none in this suite by default)
```

## Security notes

- **Transport: stdio only.** The server speaks MCP over stdin/stdout to a single local client. It does not bind a network port.
- **Outbound network egress** to three hosts: `openneuro.org`, `neurovault.org`, `eutils.ncbi.nlm.nih.gov`. If you run this in a restricted environment, allowlist those.
- **MCP shape compliance (spec rev 2025-06-18+).** Every tool declares both `inputSchema` and `outputSchema`. Successful responses populate both `content` (TextContent JSON, for legacy clients) and `structuredContent` (validated against the output schema). Errors set `isError=true` and return a typed `ToolError`. Tools carry `readOnlyHint=true`, `openWorldHint=true`, `idempotentHint=true`, `destructiveHint=false` annotations.
- **Upstream text is untrusted.** PubMed abstracts, OpenNeuro READMEs, and NeuroVault descriptions are user-supplied. They can carry prompt-injection payloads. We can't semantically sanitize them, so we wrap the most attack-prone fields (abstract, description) in an explicit `UntrustedText` envelope:
  ```json
  { "text": "...", "source": "pubmed", "truncated": false, "original_length": 1234, "trust": "untrusted_upstream" }
  ```
  Every free-text field is hard-capped at 4 KB. Every response that carries any uploader text also emits a top-level `untrusted_text_warning` reminder. Treat these fields as data, never as instructions.
- **Inputs are validated strictly.** All tool inputs use Pydantic `extra="forbid"` with length caps, regex constraints on PMIDs / accessions / DOIs, and enums on modality fields. Unknown fields raise instead of being silently dropped.
- **DOI normalization.** Inbound DOIs are normalized (lowercased, prefix-stripped, validated against `10.\d{4,9}/...`) before comparison so cross-source matches don't miss on casing differences.
- **No persistent secrets.** The only credential the server accepts is `PUBMED_API_KEY` (optional, raises PubMed rate limit). Both that and `PUBMED_EMAIL` live in `.env` (gitignored).
- **Disk cache hardening.** The NeuroVault collection index (~3 MB, no user data) is written to your OS cache directory. Files larger than 20 MB are refused on load. Cache files include a `schema_version` so a downgrade or upgrade can't misinterpret on-disk data.

## NCBI / PubMed compliance

NCBI requires every Entrez request to identify the calling tool and a contact email. The two relevant settings:

- `PUBMED_EMAIL` — set to your real address before production use. If left at the placeholder the server logs a warning at startup.
- `PUBMED_API_KEY` — optional, lifts the rate limit from 3 req/sec to 10 req/sec.

For sustained or institutional use, **register your tool with NCBI** via the [E-utilities API key page](https://www.ncbi.nlm.nih.gov/account/) so they can contact you about abuse before blocking your IP. The `tool=` parameter sent on every request is fixed to `neuro-research-discovery-mcp`.

## Provenance

Every tool call is logged by the MCP protocol with typed inputs and outputs. This creates an audit trail suitable for reproducibility-critical research workflows.

## Contributing

PRs welcome. Issues at https://github.com/ykshah1309/neuro-research-discovery-mcp/issues.

## License

MIT — see [LICENSE](LICENSE). Built by [Yash Kamlesh Shah](https://github.com/ykshah1309).
