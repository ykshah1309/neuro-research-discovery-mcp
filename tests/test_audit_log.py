"""Tests for the v0.3.1 audit log emitted by every tool call.

Why not pytest's caplog? Our audit_log has `propagate=False` to avoid
double-emission in production (root logger usually also writes to stderr).
That means caplog (which intercepts on the root logger) doesn't see our
records. We attach a list-collecting handler directly to audit_log instead.
"""

from __future__ import annotations

import json
import logging

import pytest

import neuro_research_discovery.server as srv


class _CollectingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def audit_records() -> list[str]:
    handler = _CollectingHandler()
    srv.audit_log.addHandler(handler)
    try:
        yield handler.messages
    finally:
        srv.audit_log.removeHandler(handler)


@pytest.mark.asyncio
async def test_audit_log_emits_one_line_per_call(audit_records: list[str]):
    result = await srv._call_tool("search_pubmed", {})
    assert result.isError is True
    assert len(audit_records) == 1
    parsed = json.loads(audit_records[0])
    assert parsed["tool"] == "search_pubmed"
    assert parsed["is_error"] is True
    assert parsed["error_type"] == "ValidationError"
    assert "elapsed_ms" in parsed and parsed["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_audit_log_records_arg_keys_not_values(audit_records: list[str]):
    """Argument *keys* are logged but values are not — queries can be huge,
    and we don't want to surprise users by writing their text to logs."""
    await srv._call_tool("search_pubmed", {"query": "some sensitive text"})
    assert len(audit_records) == 1
    parsed = json.loads(audit_records[0])
    assert "arg_keys" in parsed and "query" in parsed["arg_keys"]
    assert "some sensitive text" not in audit_records[0]


@pytest.mark.asyncio
async def test_audit_log_records_unknown_tool_error(audit_records: list[str]):
    await srv._call_tool("not_a_tool", {})
    assert len(audit_records) == 1
    parsed = json.loads(audit_records[0])
    assert parsed["tool"] == "not_a_tool"
    assert parsed["is_error"] is True
