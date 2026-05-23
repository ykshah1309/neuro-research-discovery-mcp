"""Tiny JSON disk cache for the NeuroVault collection index.

Stores `{built_at: float (unix), ttl: int, partial: bool, projections: [...]}`
at `~/.cache/neuro-research-discovery-mcp/neurovault_index.json` (or
`%LOCALAPPDATA%\\neuro-research-discovery-mcp\\` on Windows).

The point is to avoid the 30–150 s cold-start whenever the MCP server is
restarted (which happens often: Claude Desktop launches it fresh on every app
start). With a disk cache, restarts are near-instant.

We deliberately keep this dumb: no SQLite, no atomic-writes-with-fsync. If the
write fails or the file is corrupt, we just rebuild and overwrite.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger("neuro_research_discovery.disk_cache")


def _cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "neuro-research-discovery-mcp"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "neuro-research-discovery-mcp"


def _index_path() -> Path:
    return _cache_dir() / "neurovault_index.json"


def load_neurovault_index() -> dict[str, Any] | None:
    p = _index_path()
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "projections" not in data:
            return None
        return data
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Failed to load NeuroVault index from %s: %s", p, exc)
        return None


def save_neurovault_index(
    projections: list[dict[str, Any]],
    ttl_seconds: int,
    partial: bool,
) -> None:
    p = _index_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "built_at": time.time(),
            "ttl": int(ttl_seconds),
            "partial": bool(partial),
            "projections": projections,
        }
        # Write to a sibling temp file then replace, so a crash mid-write
        # doesn't leave us with a half-empty file.
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, p)
    except OSError as exc:
        _logger.warning("Failed to persist NeuroVault index to %s: %s", p, exc)


def is_fresh(entry: dict[str, Any]) -> bool:
    built_at = float(entry.get("built_at") or 0)
    ttl = int(entry.get("ttl") or 0)
    return (time.time() - built_at) < ttl


def is_serveable(entry: dict[str, Any], max_age_factor: float = 2.0) -> bool:
    """A cached entry is serveable if it's no older than 2x its TTL — we serve
    stale-while-revalidate up to that point, then refuse and force a rebuild."""
    built_at = float(entry.get("built_at") or 0)
    ttl = int(entry.get("ttl") or 0)
    return (time.time() - built_at) < (ttl * max_age_factor)
