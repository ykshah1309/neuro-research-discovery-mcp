# Live Demo Script — neuro-research-discovery-mcp

**When:** Wednesday May 27, 2026, 2:30 PM ET
**Where:** Fenster Hall 606, NJIT
**Audience:** BME researchers (fMRI / DTI / structural MRI)
**Pair with:** [nifti-inspector-mcp/DEMO_SCRIPT.md](../nifti-inspector-mcp/DEMO_SCRIPT.md) — combined runtime ~10 min
**Goal:** show the cross-source bridge tools doing something a plain Claude conversation cannot do — verifiable, current, typed neuroimaging research discovery.

---

## 0. Pre-demo checklist (do this **the night before**, May 26)

```powershell
cd C:\Users\yksha\bme-mcp\neuro-research-discovery-mcp
python -m pip install -e ".[dev]" -c constraints-dev.txt
python -m pytest tests/                                    # 105 should pass
where neuro-research-discovery                             # confirm console script on PATH
```

### Critical: prewarm the NeuroVault index

The first-ever NeuroVault search rebuilds a 17K-collection index from scratch — **~3 minutes**. Never let the audience watch that. Run this once the night before so the disk cache is hot:

```powershell
python scripts/bench_neurovault_cold.py --output bench_results/pre_demo.json
# Expected: ~170s, ok=true, collection_count=17333
```

After this runs, the index is on disk at `%LOCALAPPDATA%\neuro-research-discovery-mcp\neurovault_index.json` for 24 hours. The demo searches will hit it in <100 ms.

### Set PUBMED_EMAIL (mandatory for demo)

```powershell
# In .env at the project root:
PUBMED_EMAIL=ykshah1309@njit.edu
PUBMED_API_KEY=<optional — lifts rate limit from 3 to 10 req/s>
NEURO_REQUIRE_PUBMED_EMAIL=1
```

The `NEURO_REQUIRE_PUBMED_EMAIL=1` line makes the server refuse to start with a placeholder, so a bad config fails fast on your laptop instead of during the demo.

### Claude Desktop config — both MCPs side by side

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nifti-inspector": {
      "command": "nifti-inspector"
    },
    "neuro-research-discovery": {
      "command": "neuro-research-discovery",
      "env": {
        "PUBMED_EMAIL": "ykshah1309@njit.edu",
        "NEURO_REQUIRE_PUBMED_EMAIL": "1"
      }
    }
  }
}
```

Fully **Quit** Claude Desktop (system tray → Quit, not just close window) and relaunch. You should see **23 tools** in the picker (4 inspector + 19 research). If the count is wrong, the config didn't reload.

### Sanity check (~1 minute, on demo morning)

First, confirm the NeuroVault cache is still warm — **do this before any rebuild**. The
benchmark script in step 0 intentionally deletes and rebuilds the index; you do NOT
want that on demo day if the cache is already fresh.

```powershell
python -c "import asyncio; from neuro_research_discovery.tools.neurovault_tools import get_neurovault_cache_status; from neuro_research_discovery.clients.neurovault import NeuroVaultClient; from neuro_research_discovery.models import NeuroVaultCacheStatusInput; print(asyncio.run(get_neurovault_cache_status(NeuroVaultCacheStatusInput(), NeuroVaultClient())).model_dump_json(indent=2))"
```

Read the `status` field:
- `fresh` → done, nothing to do.
- `stale_but_serveable` → safe for the demo (background refresh will run on first call).
- `expired` or `missing` → re-run `python scripts/bench_neurovault_cold.py` now; budget 3 minutes.

You can also do the same from inside Claude Desktop once the server is configured:

> "Use `get_neurovault_cache_status` to check the NeuroVault cache state."

Expected: `status: "fresh"`, `collection_count: 17333`, `partial: false`. If it says `missing` or `expired`, re-run the prewarm script.

---

## 1. Opening (45 sec)

> "Earlier I showed nifti-inspector-mcp — Claude reading my local NIfTI files. This is the sibling MCP: it lets Claude reach **out** to the three big neuroimaging research repositories — OpenNeuro for raw BIDS datasets, NeuroVault for derived statistical maps, and PubMed for the published literature.
>
> The point isn't that Claude can search PubMed — anyone with a web search can do that. The point is that this MCP knows that these three sources reference each other by DOI, and it does the cross-walk for you. Watch."

---

## 2. The flagship: `comprehensive_literature_search` (2 min)

**Prompt:**

> "Use `comprehensive_literature_search` to find recent papers, datasets, and brain maps related to **default mode network in autism**."

**Expected behavior:** Claude calls the omnibus tool. ~3-4 second response. Returns a `CrossSourceResult` with:
- 5 PubMed articles (titles + abstracts in `UntrustedText` envelopes + MeSH terms)
- N OpenNeuro datasets matching the keyword
- M NeuroVault collections (mix of `keyword_match` and `doi_exact` evidence labels)
- `suggested_next_queries` list — MeSH-derived follow-ups
- `linkage_evidence` dict labeling each result

**Talking points:**

1. *"Notice the response is structured JSON, not a paragraph. Each result is typed — accession numbers, PMIDs, DOIs as actual identifiers, not prose."*
2. *"Every abstract is wrapped in an `UntrustedText` envelope tagged `trust: untrusted_upstream`. If someone uploaded a prompt-injection payload as a paper abstract, the structural wrapper tells the LLM it's data, not instructions."*
3. *"The `linkage_evidence` field is the key cross-source bit. Some NeuroVault collections came up because the keyword matched their description — labeled `keyword_match`. Others came up because their DOI exactly matches a PubMed paper this search returned — labeled `doi_exact`. The agent can choose how to weight them."*

---

## 3. The cross-source bridge: `find_neurovault_maps_for_paper` (1.5 min)

**Prompt:**

> "For PubMed paper 26178017, are there any associated brain maps on NeuroVault?"

**Expected:** Claude calls `find_neurovault_maps_for_paper(pmid="26178017")`. The tool fetches the PubMed record, extracts the DOI (`10.1038/ncomms8751`), normalizes it, then scans the cached NeuroVault index for collections whose `DOI` or `preprint_DOI` matches. Returns a `CrossSourceResult` with the paper + the matched NeuroVault collection (1345: *"Transient brain activity disentangles fMRI resting-state dynamics..."*). `linkage_evidence` shows both as `doi_exact`.

**Talking points:**

> "DOI normalization is doing the real work here — OpenNeuro and NeuroVault both store DOIs with `https://` prefixes, mixed case, sometimes free text. The MCP normalizes them all to `10.<registrant>/<suffix>` lowercase before the cross-walk. A naive keyword search would miss most of these matches."
>
> "This is also the direction that actually works well today. The opposite direction — given an OpenNeuro dataset, find papers that used it — is much sparser, because OpenNeuro's `associatedPaperDOI` metadata field is rarely populated by uploaders. That accuracy gap is exactly what v0.4's OpenAlex enrichment is designed to close: forward citation search would find any paper that cites a dataset's DOI, not just papers the uploader manually linked. Until then, the tool returns a structured `notes` field saying so rather than hallucinating."

**Verified working PMIDs (any of these work as backups):**
- `26178017` → collection 1345 (Karahanoğlu & Van De Ville, *Nat Commun* 2015)
- `24099851` → collection 109 (Hagmann et al., *NeuroImage* 2014)
- `12808459` → collection 1056 (Beckmann & Smith, *Nat Neurosci* 2003)

---

## 4. Proof of provenance: cache status + audit log (1 min)

**Prompt:**

> "Use `get_neurovault_cache_status` to show the cache state."

Returns `{status: fresh, collection_count: 17333, age_seconds: ~3600, schema_version: 2}`.

**Talking point:**

> "Two things matter here for a research demo. First: this MCP doesn't go to NeuroVault on every call — there's a 24-hour TTL'd index that gets persisted to disk so server restarts are instant. Second: every tool call we just made was logged as a single JSON line to stderr with `tool name, args, latency, cache_hits, cache_misses, error_type`. That's the audit trail. If you publish a paper that says 'we found these 12 datasets using this MCP', the audit log is your reproducibility receipt."

(Optional: if you have a terminal visible, scroll up the server stderr to show the audit lines. Otherwise, describe.)

---

## 5. The cross-MCP handoff (1.5 min) — uses **nifti-inspector** too

**Prompt:**

> "Use `list_openneuro_dataset_files` to show me the func/ files for ds000001."

Returns the file list with download URLs.

**Then:**

> "I've already downloaded `sub-01_T1w.nii.gz` from a different dataset to my local sample data. Use `load_nifti` to inspect it."

Claude calls `load_nifti` from **nifti-inspector-mcp** with the local path. Returns dimensions, voxel size, etc.

**Talking point:**

> "That's the actual workflow these MCPs unlock — discover what's out there with one server, inspect what you have locally with the other, in a single Claude conversation. The composability is the story."

---

## 6. Closing (45 sec)

> "Three repositories, four cross-source bridge tools, full DOI-normalized linkage. Audit-logged, typed, prompt-injection-bounded, and reproducible. The repo is `github.com/ykshah1309/neuro-research-discovery-mcp` — clone it, paste the JSON snippet I'll share into Claude Desktop's config, and you're doing this in five minutes. Happy to do this live in anyone's office afterward."

Mention if asked: v0.4 milestone is OpenAlex enrichment for forward-citation linking; v0.5+ adds advisory tool-response scanning. Roadmap in the repo.

---

## Pre-recorded fallback plan

If the live demo fails on the day, switch language without apology:

> "Let me show you what this looks like in a pre-recorded session — same prompts, same responses."

### Screenshots to capture the night before

Run the four prompts above in Claude Desktop, `Win+Shift+S` to capture each tool call + response, save to `demo_screenshots/`:

1. `01-comprehensive_search.png` — the omnibus result with `linkage_evidence`
2. `02-find_neurovault_maps_for_paper.png` — the DOI cross-walk (PMID 26178017 → collection 1345)
3. `03-cache_status.png` — the cache state with `fresh, 17333`
4. `04-cross_mcp_handoff.png` — list_files + load_nifti in one chat

---

## Common failure modes (in order of likelihood)

| Symptom | Likely cause | Fix |
|---|---|---|
| Tools don't appear in Claude Desktop | Config didn't reload | Fully quit Claude (system tray → Quit), reopen. Verify the JSON parses with `python -m json.tool < claude_desktop_config.json`. |
| First search hangs ~3 minutes | NeuroVault disk cache wasn't prewarmed | Run `python scripts/bench_neurovault_cold.py` once before going on stage. |
| Server refuses to start | `NEURO_REQUIRE_PUBMED_EMAIL=1` + placeholder email | Set real `PUBMED_EMAIL` in the config's `env` block or in `.env`. |
| `find_neurovault_maps_for_paper` returns 0 collections | PubMed record has no DOI, or DOI isn't on any NeuroVault collection | Use a backup PMID from the list above (26178017, 24099851, 12808459 all verified). Or switch to `find_datasets_for_topic` as a fallback. |
| `find_papers_using_dataset` returns 0 papers | OpenNeuro `associatedPaperDOI` is empty for most datasets | Known limitation — use the reverse direction (`find_neurovault_maps_for_paper`) instead. v0.4 OpenAlex enrichment is the planned fix. |
| PubMed 429 errors mid-demo | Anonymous rate limit (3/s) hit | Add `PUBMED_API_KEY` to the env block. |
| Tool count is 23 but feels wrong | If you only see 19, nifti-inspector isn't loaded. If only 4, this MCP isn't loaded. | Check both mcpServers blocks in the JSON config. |

---

## Backup prompts (use if a primary prompt misfires)

- *"Search PubMed for the most-cited papers on resting-state fMRI from the last 3 years."*
- *"For collection 457 on NeuroVault, what are the publication details?"*
- *"What OpenNeuro datasets are available for diffusion imaging?"*

These exercise simpler single-tool paths and have lower failure surface than the bridge tools.

---

## Pacing reminder

Total budget for this MCP: **6–7 minutes** if it's the second half of a combined demo with nifti-inspector. **8–9 minutes** if it's standalone. Don't run long — the Q&A is where the BME folks decide whether to actually clone the repo.

---

## Optional segment: zero-install web UI for the audience (3 min)

If your audience doesn't have Claude Desktop and you want them to actually try the tools during the talk, hand them a URL.

### Pre-event setup (the same night as the screenshot capture)

```powershell
cd C:\Users\yksha\bme-mcp\neuro-research-discovery-mcp
pip install -e ".[web]" -c constraints-dev.txt
```

Find your LAN IP (Settings → Network → Wi-Fi details, or `ipconfig`). Write it on the whiteboard or generate a QR code for `http://<your-ip>:8000/`.

### Start the server right before going on stage

```powershell
neuro-research-discovery-web --host 0.0.0.0 --port 8000
```

Test from your phone first — the same SSID as the demo laptop — make sure the page loads and one tool call succeeds. If your network blocks LAN-to-LAN traffic (some campus networks do), fall back to a phone hotspot or to the Claude Desktop demo only.

### What the audience sees

- A header pill that says `cache: fresh · 17,333 collections` (if you prewarmed properly).
- A left rail with all 19 tools grouped by family.
- A center form auto-generated from each tool's input schema. Required fields are starred. Enums (like `modality`) are dropdowns.
- The right rail shows a live audit log streaming every tool call from every guest — so they see each other's queries land in real time with cache hit/miss counters.

### Suggested first prompts to seed the audience

1. *Pick `search_neurovault_collections`, type `Stroop`, hit Run.* → 3 collections, sub-second (from the warm cache).
2. *Pick `find_neurovault_maps_for_paper`, type `26178017`, hit Run.* → confirms the DOI-exact cross-walk in 1-2s.
3. *Pick `get_neurovault_cache_status`, hit Run.* → shows the cache is hot.

The live audit log on the right is the talking point: *"every one of those is one HTTP call, logged with cache hit/miss, arg keys but not values, latency, and a `via: web` marker. Same audit trail my MCP shows when Claude Desktop calls the tools."*

### Risks of the web segment

- **CORS / LAN routing.** Some networks isolate clients. Test with your phone before going on stage.
- **One disk cache shared across guests.** First click on a NeuroVault search by the first guest is fast (cache is hot); but if you forgot to prewarm, that first click is a 3-minute stall for everyone watching.
- **No auth.** Anyone on the network can hit it. Fine for a controlled event; do NOT leave it running afterwards on a campus network.
- **PUBMED rate limit shared.** All guests hitting `search_pubmed` count against the same 3 req/sec anonymous quota. Set `PUBMED_API_KEY` for the demo to lift that to 10/sec.

### Fallback if the web UI fails on stage

You still have the Claude Desktop demo as the primary. The web UI is the audience-participation bonus. If it breaks, just say "I'll send the URL after the talk so you can try it from your seat or your office" — most won't notice, and you've kept the main demo intact.
