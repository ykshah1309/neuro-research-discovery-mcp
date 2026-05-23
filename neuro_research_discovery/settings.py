"""Global settings loaded from environment (.env via python-dotenv)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=False)

PUBMED_API_KEY: str | None = os.environ.get("PUBMED_API_KEY") or None
PUBMED_EMAIL: str = os.environ.get("PUBMED_EMAIL") or "neuro-research-discovery-mcp@example.com"
PUBMED_TOOL: str = "neuro-research-discovery-mcp"

# Rate limit (requests per second) per upstream. PubMed depends on api key presence.
PUBMED_RATE_PER_SEC: float = 9.0 if PUBMED_API_KEY else 2.5  # leave headroom under 10 / 3
OPENNEURO_RATE_PER_SEC: float = 10.0
NEUROVAULT_RATE_PER_SEC: float = 10.0

# Cache TTLs (seconds)
DEFAULT_CACHE_TTL: int = 3600  # 1 hour for most calls
NEUROVAULT_INDEX_TTL: int = int(os.environ.get("NEUROVAULT_INDEX_TTL", "86400"))  # 24 h

# HTTP timeouts (seconds)
HTTP_CONNECT_TIMEOUT: float = 10.0
HTTP_READ_TIMEOUT: float = 60.0

# OpenNeuro
OPENNEURO_GRAPHQL_URL: str = "https://openneuro.org/crn/graphql"

# NeuroVault
NEUROVAULT_API_BASE: str = "https://neurovault.org/api"
