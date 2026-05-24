"""Benchmark the NeuroVault cold-cache rebuild.

Why: the rebuild is the one ugly latency cliff in this MCP. A weekly CI run
that measures it and uploads the result as an artifact makes regressions
visible — e.g. if NeuroVault tightens rate limits or grows the collection
table, we'd see the time creep up over weeks.

Usage:
    python scripts/bench_neurovault_cold.py [--output PATH]

Output: a single-line compact JSON object on stdout (suitable for line-
oriented log ingestion) and optionally a pretty-printed copy at --output
for human review / CI artifact upload. Shape:

    {"timestamp":"2026-05-23T20:55:12Z","elapsed_seconds":168.2,
     "collection_count":17333,"partial":false,"ok":true,
     "schema_version":2,"platform":"win32","python":"3.12.10"}

The script deletes any existing on-disk index first so we measure a true
cold rebuild, not a disk-cache load. It restores nothing; the rebuild writes
a fresh index file when it succeeds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


async def _run() -> dict:
    # Imported lazily so a missing install gives a clearer error than a
    # top-of-file ImportError.
    from neuro_research_discovery import disk_cache
    from neuro_research_discovery.clients.neurovault import NeuroVaultClient

    # Delete any existing on-disk index so this is a true cold run.
    idx_path = disk_cache._index_path()
    if idx_path.is_file():
        idx_path.unlink()

    client = NeuroVaultClient()
    started = time.monotonic()
    ok = True
    error: str | None = None
    try:
        index = await client.get_index(force_refresh=True)
        collection_count = len(index)
        partial = bool(getattr(client, "index_partial", False))
    except Exception as exc:  # noqa: BLE001 — benchmark must not crash on upstream failure
        ok = False
        error = f"{type(exc).__name__}: {exc}"
        collection_count = 0
        partial = True
    finally:
        await client.aclose()

    elapsed = time.monotonic() - started
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(elapsed, 2),
        "collection_count": collection_count,
        "partial": partial,
        "ok": ok,
        "error": error,
        "schema_version": disk_cache.NEUROVAULT_INDEX_SCHEMA_VERSION,
        "platform": sys.platform,
        "python": platform.python_version(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=None,
                    help="If set, also write the result JSON to this path.")
    args = ap.parse_args()

    result = asyncio.run(_run())
    # Stdout: compact, single-line JSON for line-oriented log ingestion.
    print(json.dumps(result, separators=(",", ":")))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Artifact file: pretty-printed for human review.
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    # Non-zero exit if the run failed so CI can flag it.
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
