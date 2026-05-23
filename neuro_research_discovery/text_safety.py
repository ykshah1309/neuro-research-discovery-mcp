"""Truncation budgets for upstream-supplied free text.

We can't semantically sanitize prompt-injection payloads in third-party text
(that would require an LLM classifier and would still be imperfect). What we
*can* do is bound the size: cap every free-text field at MAX_FIELD_LEN chars
and cap collection lists at MAX_LIST_ITEMS items, so a single tool response
can't blow up the model's context budget.

Callers are also responsible for emitting the `untrusted_text_warning` field
on outputs that carry uploader text — defined in models.UNTRUSTED_WARNING.
"""

from __future__ import annotations

# Per-field hard caps. Most PubMed abstracts fit under 5K; OpenNeuro READMEs
# can be 50K+. We trim aggressively rather than try to be clever.
MAX_FIELD_LEN = 4_000
MAX_TITLE_LEN = 500
MAX_AUTHORS_LEN = 1_000
MAX_LIST_ITEMS = 50

# Hard cap on file listing entries; per-call override possible later if needed.
MAX_FILES_PER_LISTING = 200


def truncate(text: str | None, max_len: int = MAX_FIELD_LEN) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    tail = f"... [truncated, {len(text)} chars total, kept {max_len}]"
    return text[: max_len - len(tail)] + tail


def truncate_title(text: str | None) -> str:
    return truncate(text, MAX_TITLE_LEN)


def truncate_authors(text: str | None) -> str:
    return truncate(text, MAX_AUTHORS_LEN)


def cap_list(items: list, max_items: int = MAX_LIST_ITEMS) -> tuple[list, bool]:
    """Return (capped_list, was_truncated)."""
    if len(items) <= max_items:
        return items, False
    return items[:max_items], True
