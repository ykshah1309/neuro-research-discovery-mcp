"""Structured tool errors. We return these as JSON to the MCP client instead of raising."""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel

ErrorType = Literal[
    "rate_limited",
    "not_found",
    "api_unreachable",
    "upstream_error",
    "bad_input",
    "timeout",
    "internal_error",
]


class ToolError(BaseModel):
    error_type: ErrorType
    human_readable_message: str
    upstream_status_code: int | None = None
    suggested_action: str | None = None


def classify_exception(exc: BaseException) -> ToolError:
    """Map a raised exception to a structured ToolError."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return ToolError(
                error_type="not_found",
                human_readable_message=f"Upstream resource not found (HTTP 404): {exc.request.url}",
                upstream_status_code=status,
                suggested_action="Verify the identifier (accession number, PMID, or collection/image ID).",
            )
        if status == 429:
            return ToolError(
                error_type="rate_limited",
                human_readable_message="Upstream rate limit reached.",
                upstream_status_code=status,
                suggested_action="Retry in 30–60 seconds, or set PUBMED_API_KEY for higher PubMed limits.",
            )
        if 400 <= status < 500:
            return ToolError(
                error_type="bad_input",
                human_readable_message=f"Upstream rejected the request: HTTP {status}.",
                upstream_status_code=status,
                suggested_action="Check the input parameters.",
            )
        return ToolError(
            error_type="upstream_error",
            human_readable_message=f"Upstream returned HTTP {status}.",
            upstream_status_code=status,
            suggested_action="Retry shortly; the upstream service may be having issues.",
        )
    if isinstance(exc, httpx.TimeoutException):
        return ToolError(
            error_type="timeout",
            human_readable_message=f"Upstream request timed out: {exc}",
            suggested_action="Retry; large datasets or first-time index builds can take 5–10 seconds.",
        )
    if isinstance(exc, httpx.TransportError):
        return ToolError(
            error_type="api_unreachable",
            human_readable_message=f"Could not reach upstream: {exc}",
            suggested_action="Check your network connection; the upstream may be down.",
        )
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return ToolError(
            error_type="bad_input",
            human_readable_message=f"Invalid input: {exc}",
            suggested_action="Check the parameters passed to the tool.",
        )
    return ToolError(
        error_type="internal_error",
        human_readable_message=f"{type(exc).__name__}: {exc}",
        suggested_action="Report this issue at https://github.com/ykshah1309/neuro-research-discovery-mcp/issues",
    )
