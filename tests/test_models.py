"""Boundary-validation tests for the input models.

These confirm the v0.2.0 upgrades from UPGRADE_PLAN.md Tier 1a are in force.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from neuro_research_discovery.models import (
    FindNeuroVaultMapsForPaperInput,
    FindPapersUsingDatasetInput,
    GetOpenNeuroDatasetInput,
    GetPubMedArticleInput,
    SearchOpenNeuroInput,
    SearchPubMedInput,
)


# ---- extra=forbid ----

def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        SearchOpenNeuroInput(query="autism", unknown_field="x")  # type: ignore[call-arg]


# ---- query length caps ----

def test_query_too_long_rejected():
    with pytest.raises(ValidationError):
        SearchPubMedInput(query="x" * 1001)


def test_query_empty_rejected():
    with pytest.raises(ValidationError):
        SearchOpenNeuroInput(query="")


# ---- modality enum ----

def test_invalid_modality_rejected():
    with pytest.raises(ValidationError):
        SearchOpenNeuroInput(query="autism", modality="bogus")  # type: ignore[arg-type]


def test_valid_modality_accepted():
    SearchOpenNeuroInput(query="autism", modality="mri")  # no raise


# ---- accession / pmid / doi patterns ----

def test_bad_accession_rejected():
    with pytest.raises(ValidationError):
        GetOpenNeuroDatasetInput(accession_number="not-an-accession")


def test_accession_accepted():
    GetOpenNeuroDatasetInput(accession_number="ds000001")
    FindPapersUsingDatasetInput(openneuro_accession="ds002785")


def test_bad_pmid_rejected():
    with pytest.raises(ValidationError):
        GetPubMedArticleInput(pmid="not-a-pmid")


def test_pmid_with_letters_rejected():
    with pytest.raises(ValidationError):
        FindNeuroVaultMapsForPaperInput(pmid="123abc")


def test_pmid_accepted():
    GetPubMedArticleInput(pmid="12345678")


# ---- max_results bounds ----

def test_oversized_max_results_rejected():
    with pytest.raises(ValidationError):
        SearchPubMedInput(query="autism", max_results=10_000)


def test_zero_max_results_rejected():
    with pytest.raises(ValidationError):
        SearchPubMedInput(query="autism", max_results=0)
