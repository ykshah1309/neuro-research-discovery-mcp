"""HTTP / web front-end for the neuro-research-discovery MCP.

Wraps the existing `tools/` and `clients/` layer in a FastAPI app so the
same 19 typed tools that ship over MCP stdio are also reachable from any
browser. No LLM involved — the UI is a form generator over the Pydantic
input schemas.

Start the server with:

    python -m neuro_research_discovery.web

Bind to all interfaces (for an event where guests use phones on the LAN):

    python -m neuro_research_discovery.web --host 0.0.0.0
"""
