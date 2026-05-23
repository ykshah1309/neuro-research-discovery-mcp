"""Tests for the truncation budget — Tier 1b output hardening."""

from __future__ import annotations

from neuro_research_discovery.text_safety import (
    MAX_FIELD_LEN,
    cap_list,
    truncate,
    truncate_title,
)


def test_truncate_short_string_unchanged():
    assert truncate("hello") == "hello"


def test_truncate_long_string_capped():
    long = "x" * 10_000
    out = truncate(long)
    assert len(out) <= MAX_FIELD_LEN
    assert "[truncated" in out
    assert out.startswith("x")


def test_truncate_handles_none():
    assert truncate(None) == ""


def test_truncate_title_uses_smaller_cap():
    out = truncate_title("y" * 1_000)
    assert len(out) <= 500
    assert "[truncated" in out


def test_cap_list_under_max():
    items, was_truncated = cap_list([1, 2, 3])
    assert items == [1, 2, 3] and was_truncated is False


def test_cap_list_over_max_truncates():
    items, was_truncated = cap_list(list(range(100)), max_items=10)
    assert len(items) == 10 and was_truncated is True
