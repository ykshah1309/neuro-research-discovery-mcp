"""DOI normalization.

DOIs arrive from upstreams in many shapes:
- "10.1234/foo"
- "https://doi.org/10.1234/foo"
- "http://dx.doi.org/10.1234/foo"
- "doi:10.1234/foo"
- "DOI: 10.1234/foo"
- "  10.1234/Foo  " (case + whitespace)

We need a single canonical form for cross-source lookups (NeuroVault DOI
match, PubMed DOI->PMID). The canonical form here is:
- lowercased prefix and suffix
- no URL prefix
- no leading "doi:" / "DOI:"
- stripped whitespace
- must match the standard DOI shape: 10.<registrant>/<suffix>
"""

from __future__ import annotations

import re

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "dx.doi.org/",
)

_DOI_LEADING = re.compile(r"^\s*(doi\s*:\s*)", re.IGNORECASE)
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")


def normalize_doi(raw: str | None) -> str | None:
    """Return a canonical lowercased DOI string, or None if not recognizable.

    Examples
    --------
    >>> normalize_doi("https://doi.org/10.1234/Foo")
    '10.1234/foo'
    >>> normalize_doi("DOI: 10.1234/bar")
    '10.1234/bar'
    >>> normalize_doi("not a doi")
    >>> normalize_doi(None)
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    s = _DOI_LEADING.sub("", s)
    lowered = s.lower()
    for pref in _DOI_PREFIXES:
        if lowered.startswith(pref):
            s = s[len(pref):]
            break
    s = s.strip().lower()
    if not _DOI_PATTERN.match(s):
        return None
    return s


def equal_dois(a: str | None, b: str | None) -> bool:
    """Case- and prefix-insensitive DOI equality."""
    na, nb = normalize_doi(a), normalize_doi(b)
    return na is not None and na == nb
