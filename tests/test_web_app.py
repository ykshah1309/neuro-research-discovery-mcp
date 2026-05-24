"""Smoke tests for the FastAPI web wrapper.

Uses fastapi.testclient + the standard MockTransport patching from conftest
to keep these tests offline-safe. The point is to prove:
- the tool catalog round-trips with input + output schemas,
- POST /api/tools/{name} hits the same _dispatch + audit path as MCP stdio,
- bad input returns isError=true with a structured ToolError shape,
- the audit log emits a `via: "web"` marker so analysis pipelines can
  distinguish web traffic from Claude-Desktop traffic.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from neuro_research_discovery.web.app import app
from tests.conftest import (
    FakeEntrez,
    make_esearch_xml,
    make_pubmed_efetch_xml,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_api_version(client: TestClient):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json()["version"]


def test_api_tools_catalog_has_19_with_schemas(client: TestClient):
    r = client.get("/api/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert len(tools) == 19
    for t in tools:
        assert t["name"]
        assert t["inputSchema"]
        assert t["outputSchema"], f"{t['name']} missing outputSchema"


def test_api_call_tool_rejects_bad_input(client: TestClient):
    r = client.post("/api/tools/search_pubmed", json={"max_results": 99999})
    body = r.json()
    assert body["isError"] is True
    assert body["structuredContent"]["error_type"] == "bad_input"


def test_api_call_tool_unknown_tool(client: TestClient):
    r = client.post("/api/tools/not_a_real_tool", json={})
    body = r.json()
    assert body["isError"] is True


def test_api_call_tool_search_pubmed_happy_path(fake_entrez: FakeEntrez, client: TestClient):
    fake_entrez.esearch_response = make_esearch_xml(["111"], count=42)
    fake_entrez.efetch_response = make_pubmed_efetch_xml([{
        "pmid": "111", "title": "T", "authors": [{"first": "A", "last": "B"}],
        "journal": "J", "year": "2024", "abstract": "abs", "doi": "10.1234/x",
        "mesh": [],
    }])
    r = client.post("/api/tools/search_pubmed", json={"query": "autism", "max_results": 1})
    body = r.json()
    assert body["isError"] is False
    sc = body["structuredContent"]
    assert sc["total_hits"] == 42
    assert sc["articles"][0]["pmid"] == "111"
    # UntrustedText envelope preserved over HTTP
    assert sc["articles"][0]["abstract"]["trust"] == "untrusted_upstream"


def test_audit_log_emits_via_web_marker(client: TestClient):
    """The audit logger has propagate=False so caplog can't see it. We
    follow the same pattern as test_audit_log.py: attach a list-collecting
    handler directly to the audit logger for the duration of the test."""
    import logging
    import neuro_research_discovery.server as srv

    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())  # type: ignore[method-assign]
    srv.audit_log.addHandler(handler)
    try:
        client.post("/api/tools/not_a_real_tool", json={})
    finally:
        srv.audit_log.removeHandler(handler)

    assert records, "no audit record emitted"
    parsed = json.loads(records[-1])
    assert parsed["via"] == "web"
    assert parsed["tool"] == "not_a_real_tool"
    assert parsed["is_error"] is True
