"""FastAPI app — HTTP wrapper over the MCP server's typed tools.

Endpoints:
- GET  /                       → the single-page web UI
- GET  /api/tools              → catalog: name, description, input schema, output schema
- POST /api/tools/{name}       → execute a tool; body = JSON arguments
- GET  /api/cache/status       → NeuroVault cache state (pill in the header)
- GET  /api/audit/stream       → Server-Sent Events stream of the audit log

The tool dispatch reuses the MCP server's `_dispatch` function so behavior
is identical to running over Claude Desktop's stdio — same input validation,
same `UntrustedText` envelopes, same audit logging, same cache.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from .. import __version__
from ..cache import cache_stats
from ..errors import classify_exception
from ..models import NeuroVaultCacheStatusInput
from ..server import _dispatch, _list_tools, audit_log
from ..tools import neurovault_tools
from .audit_sink import WebAuditSink

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Shared audit sink — attached to the audit logger so every tool call
# (regardless of caller — Claude Desktop OR this web UI) lands in the stream.
_audit_sink = WebAuditSink(backlog=200)
_audit_sink.setFormatter(logging.Formatter("%(message)s"))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _audit_sink.attach_loop(asyncio.get_running_loop())
    if _audit_sink not in audit_log.handlers:
        audit_log.addHandler(_audit_sink)
    try:
        yield
    finally:
        if _audit_sink in audit_log.handlers:
            audit_log.removeHandler(_audit_sink)


app = FastAPI(
    title="neuro-research-discovery",
    version=__version__,
    description="Web UI for the neuro-research-discovery MCP tools.",
    lifespan=_lifespan,
)


# ----- routes -----

@app.get("/", response_class=FileResponse, include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/version")
async def api_version() -> dict[str, str]:
    return {"version": __version__}


@app.get("/api/tools")
async def api_tools() -> dict[str, Any]:
    """Catalog every tool with its input + output schemas for the form UI."""
    tools = await _list_tools()
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
                "outputSchema": t.outputSchema,
                "annotations": {
                    "readOnlyHint": t.annotations.readOnlyHint if t.annotations else None,
                    "openWorldHint": t.annotations.openWorldHint if t.annotations else None,
                } if t.annotations else None,
            }
            for t in tools
        ]
    }


@app.post("/api/tools/{name}")
async def api_call_tool(name: str, request: Request) -> JSONResponse:
    """Execute one tool. Mirrors the MCP CallToolResult shape exactly."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

    t0 = time.monotonic()
    err_type: str | None = None
    is_error = False
    structured: Any = None
    stats_token = cache_stats.set({"hits": 0, "misses": 0})
    try:
        try:
            result = await _dispatch(name, body)
        except ValidationError as exc:
            err_type = "ValidationError"
            is_error = True
            structured = {
                "error_type": "bad_input",
                "human_readable_message": f"Input validation failed: {exc}",
                "suggested_action": "Check the tool's inputSchema and re-issue.",
            }
        except Exception as exc:  # noqa: BLE001
            err_type = type(exc).__name__
            is_error = True
            structured = classify_exception(exc).model_dump(mode="json")
        else:
            structured = result.model_dump(mode="json")

        return JSONResponse(
            content={
                "isError": is_error,
                "structuredContent": structured,
            }
        )
    finally:
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 1)
        stats = cache_stats.get() or {"hits": 0, "misses": 0}
        cache_stats.reset(stats_token)
        try:
            audit_log.info(json.dumps({
                "ts": round(time.time(), 3),
                "tool": name,
                "arg_keys": sorted((body or {}).keys()),
                "elapsed_ms": elapsed_ms,
                "is_error": is_error,
                "error_type": err_type,
                "cache_hits": stats["hits"],
                "cache_misses": stats["misses"],
                "via": "web",
            }))
        except Exception:  # noqa: BLE001
            pass


@app.get("/api/cache/status")
async def api_cache_status() -> dict[str, Any]:
    from ..clients.neurovault import NeuroVaultClient
    # We use a fresh client each time to inspect the on-disk + per-process
    # state, but the disk cache is shared so the answer is meaningful even
    # without a long-lived client instance.
    client = NeuroVaultClient()
    try:
        status = await neurovault_tools.get_neurovault_cache_status(
            NeuroVaultCacheStatusInput(), client
        )
        return status.model_dump(mode="json")
    finally:
        await client.aclose()


@app.get("/api/audit/stream")
async def api_audit_stream(request: Request) -> EventSourceResponse:
    """SSE stream of audit log lines. One JSON object per event."""
    async def event_gen():
        async for line in _audit_sink.stream():
            if await request.is_disconnected():
                break
            yield {"event": "audit", "data": line}
    return EventSourceResponse(event_gen())


# Static assets (css, js, favicon). Mount after API routes so /api/* wins.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
