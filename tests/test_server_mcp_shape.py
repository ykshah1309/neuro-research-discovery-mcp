"""Tests for v0.3 MCP-spec compliance: outputSchema, structuredContent, isError, annotations."""

from __future__ import annotations

import pytest

import neuro_research_discovery.server as srv


@pytest.mark.asyncio
async def test_every_tool_has_input_and_output_schema():
    tools = await srv._list_tools()
    # 17 core neuro tools + 2 v0.3.1 cache-admin tools = 19
    assert len(tools) == 19
    for t in tools:
        assert t.inputSchema, f"{t.name} missing inputSchema"
        assert t.outputSchema, f"{t.name} missing outputSchema"


@pytest.mark.asyncio
async def test_every_tool_carries_readonly_open_world_annotations():
    tools = await srv._list_tools()
    for t in tools:
        ann = t.annotations
        assert ann is not None, f"{t.name} missing annotations"
        assert ann.readOnlyHint is True, f"{t.name} should be readOnly"
        assert ann.openWorldHint is True, f"{t.name} should be openWorld"
        assert ann.destructiveHint is False, f"{t.name} should be non-destructive"


@pytest.mark.asyncio
async def test_call_tool_returns_calltoolresult_with_structured_content():
    """A successful call should produce a CallToolResult with structuredContent
    populated AND TextContent for backwards compat AND isError=False."""
    # search_pubmed needs a real upstream; we already validate the wiring
    # elsewhere. Use the dispatch path via a tool that takes only validated
    # input — bad_input rejection.
    result = await srv._call_tool("search_pubmed", {})  # missing required query
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error_type"] == "bad_input"
    assert result.content and result.content[0].type == "text"


@pytest.mark.asyncio
async def test_unknown_tool_returns_structured_error():
    result = await srv._call_tool("nope_not_a_tool", {})
    assert result.isError is True
    assert result.structuredContent is not None
    assert "Unknown tool" in result.structuredContent["human_readable_message"]
