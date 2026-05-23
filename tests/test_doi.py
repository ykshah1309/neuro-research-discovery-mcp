"""Tests for the DOI normalization helper."""

from __future__ import annotations

import pytest

from neuro_research_discovery.doi import equal_dois, normalize_doi


@pytest.mark.parametrize("raw,expected", [
    ("10.1234/foo", "10.1234/foo"),
    ("10.1234/FOO", "10.1234/foo"),
    ("https://doi.org/10.1234/foo", "10.1234/foo"),
    ("http://dx.doi.org/10.1234/foo", "10.1234/foo"),
    ("doi:10.1234/foo", "10.1234/foo"),
    ("DOI: 10.1234/foo", "10.1234/foo"),
    ("  10.1234/foo  ", "10.1234/foo"),
])
def test_normalize_accepts_known_shapes(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "not a doi", "10/foo", "10.x/foo",
    "12345/foo", "https://example.com/foo", "10.1234/", "10. 1234/foo",
])
def test_normalize_rejects_garbage(raw):
    assert normalize_doi(raw) is None


def test_equal_dois_handles_casing_and_prefix():
    assert equal_dois("10.1234/FOO", "https://doi.org/10.1234/foo") is True
    assert equal_dois("10.1234/foo", "10.5555/bar") is False
    assert equal_dois(None, "10.1234/foo") is False
