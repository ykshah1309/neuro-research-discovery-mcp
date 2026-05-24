"""Guard against documentation drift.

A surprisingly common source of v0.x review findings is README claims that
disagree with the code: stale tool counts, stale field names. This test
asserts the core invariants:

- README's headline tool-count number matches `len(_list_tools())`.
- Every tool name listed by `_list_tools()` appears at least once in README.
- README mentions every field name we wrap in `UntrustedText` so the security
  section can't quietly understate coverage.

When you intentionally add or rename a tool / wrapped field, update the
README in the same commit and these checks will pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import neuro_research_discovery.server as srv

README = Path(__file__).resolve().parent.parent / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_readme_tool_count_matches_server(readme_text: str):
    tools = await srv._list_tools()
    actual = len(tools)
    # Look for the headline phrase: "**N typed tools**" or "N tools will appear"
    matches = re.findall(r"\*\*(\d+) typed tools\*\*|The (\d+) tools will appear", readme_text)
    found_numbers = {int(n) for tup in matches for n in tup if n}
    assert actual in found_numbers, (
        f"README mentions tool counts {sorted(found_numbers) or 'none'} but "
        f"_list_tools() returns {actual}. Update README headline."
    )


@pytest.mark.asyncio
async def test_every_tool_name_appears_in_readme(readme_text: str):
    tools = await srv._list_tools()
    missing = [t.name for t in tools if t.name not in readme_text]
    assert not missing, f"Tools missing from README: {missing}"


def test_readme_mentions_envelope_coverage(readme_text: str):
    """The README security section must enumerate every field we wrap in
    UntrustedText. If you add a new wrapped field, mention it here."""
    must_mention = ["abstract", "description", "title", "name", "journal", "authors"]
    missing = [w for w in must_mention if w not in readme_text]
    assert not missing, (
        f"README doesn't mention these wrapped fields in the security section: {missing}"
    )


def test_readme_mentions_audit_log_fields(readme_text: str):
    """If we name fields in the audit log shape, README should too. Keep this
    aligned with the JSON line emitted in server._call_tool."""
    must_mention = ["cache_hits", "cache_misses", "elapsed_ms", "is_error"]
    missing = [w for w in must_mention if w not in readme_text]
    assert not missing, f"Audit log fields missing from README: {missing}"
