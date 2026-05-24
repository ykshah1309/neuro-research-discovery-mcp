"""Guard against documentation drift.

A surprisingly common source of v0.x review findings is README claims that
disagree with the code: stale tool counts, stale field names. This test
asserts the core invariants section-by-section so a passing check is a
strong statement rather than a coincidence:

- README's headline tool-count number matches `len(_list_tools())`.
- Every tool name listed by `_list_tools()` appears at least once in README.
- README's "## Security notes" section explicitly enumerates every field
  we wrap in `UntrustedText`.
- README's "## Audit logging" section explicitly enumerates every key
  emitted by the audit logger.

The section-scoped checks (third and fourth) are deliberately stricter than
a whole-document substring search: a word like "title" naturally appears in
many places, but we want it to appear in the *security* enumeration.

When you intentionally add or rename a tool / wrapped field / audit field,
update the README in the same commit and these checks will pass.
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


def _section(text: str, heading: str) -> str:
    """Extract the body of a `## <heading>` section up to the next `## ` heading."""
    pattern = rf"(?ms)^## +{re.escape(heading)}\b.*?(?=^## |\Z)"
    match = re.search(pattern, text)
    assert match, f"README is missing required section: '## {heading}'"
    return match.group(0)


# ---- tool inventory drift ----

@pytest.mark.asyncio
async def test_readme_tool_count_matches_server(readme_text: str):
    tools = await srv._list_tools()
    actual = len(tools)
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


# ---- section-aware security drift ----

def test_security_section_enumerates_every_wrapped_field(readme_text: str):
    """The README security section must mention every field we wrap.

    Scope: only the '## Security notes' section. A keyword appearing elsewhere
    in the README (e.g. 'authors' inside an example query) does not count.
    """
    security = _section(readme_text, "Security notes")
    must_mention = ["abstract", "description", "title", "name", "journal", "authors"]
    missing = [w for w in must_mention if w not in security]
    assert not missing, (
        f"Security notes section is missing wrapped field names: {missing}. "
        "If you wrap a new field in UntrustedText, list it here."
    )


def test_audit_logging_section_enumerates_every_field(readme_text: str):
    """The README audit-log section must enumerate every key the logger emits.

    Scope: only the '## Audit logging' section. Keeps the audit-log JSON
    contract aligned with the docs for log-analysis pipelines.
    """
    audit = _section(readme_text, "Audit logging")
    must_mention = [
        "ts", "tool", "arg_keys", "elapsed_ms",
        "is_error", "error_type", "cache_hits", "cache_misses",
    ]
    missing = [w for w in must_mention if w not in audit]
    assert not missing, (
        f"Audit logging section is missing field names: {missing}. "
        "If you add a key to the audit JSON in server._call_tool, document it here."
    )
