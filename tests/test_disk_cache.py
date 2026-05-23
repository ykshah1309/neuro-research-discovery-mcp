"""Tests for the JSON disk cache used by the NeuroVault index."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from neuro_research_discovery import disk_cache


@pytest.fixture(autouse=True)
def _isolated_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point the disk cache at a tmp directory for every test."""
    def fake_cache_dir():
        return tmp_path
    monkeypatch.setattr(disk_cache, "_cache_dir", fake_cache_dir)
    monkeypatch.setattr(disk_cache, "_index_path", lambda: tmp_path / "neurovault_index.json")
    return tmp_path


def test_load_returns_none_when_file_missing():
    assert disk_cache.load_neurovault_index() is None


def test_save_then_load_roundtrip():
    projs = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    disk_cache.save_neurovault_index(projs, ttl_seconds=3600, partial=False)
    loaded = disk_cache.load_neurovault_index()
    assert loaded is not None
    assert loaded["projections"] == projs
    assert loaded["ttl"] == 3600
    assert loaded["partial"] is False
    assert isinstance(loaded["built_at"], float)


def test_is_fresh_true_for_recent_entry():
    entry = {"built_at": time.time(), "ttl": 3600, "projections": []}
    assert disk_cache.is_fresh(entry) is True


def test_is_fresh_false_for_old_entry():
    entry = {"built_at": time.time() - 7200, "ttl": 3600, "projections": []}
    assert disk_cache.is_fresh(entry) is False


def test_is_serveable_within_2x_ttl():
    entry = {"built_at": time.time() - 5000, "ttl": 3600, "projections": []}
    assert disk_cache.is_fresh(entry) is False
    assert disk_cache.is_serveable(entry) is True


def test_is_serveable_false_beyond_2x_ttl():
    entry = {"built_at": time.time() - 10_000, "ttl": 3600, "projections": []}
    assert disk_cache.is_serveable(entry) is False


def test_load_returns_none_on_corrupt_file(tmp_path: Path):
    (tmp_path / "neurovault_index.json").write_text("not valid json", encoding="utf-8")
    assert disk_cache.load_neurovault_index() is None
