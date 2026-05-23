"""Pydantic models for every tool's input and output.

Organized by family: OpenNeuro (A), NeuroVault (B), PubMed (C), Bridge (D).
All output models can be serialized cleanly by .model_dump_json().
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# =========================================================================
# Family A — OpenNeuro
# =========================================================================

class SearchOpenNeuroInput(BaseModel):
    query: str = Field(description="Free-text keyword(s); e.g. 'autism', 'n-back'.")
    modality: str | None = Field(
        default=None,
        description=(
            "Optional top-level modality filter. OpenNeuro values (lowercase): "
            "'mri', 'eeg', 'meg', 'ieeg', 'pet', 'nirs'. "
            "BIDS sub-modalities ('anat', 'func', 'dwi') are filtered client-side."
        ),
    )
    max_results: int = Field(default=10, ge=1, le=50)


class GetOpenNeuroDatasetInput(BaseModel):
    accession_number: str = Field(description="OpenNeuro accession, e.g. 'ds000001'.")


class ListOpenNeuroDatasetFilesInput(BaseModel):
    accession_number: str
    modality: str | None = Field(
        default=None,
        description="Optional BIDS sub-modality directory filter (anat / func / dwi / fmap / ...).",
    )


class GetOpenNeuroPublicationsInput(BaseModel):
    accession_number: str


class OpenNeuroDataset(BaseModel):
    accession_number: str
    title: str
    description: str
    modalities: list[str]
    num_subjects: int
    num_sessions: int
    tasks: list[str]
    species: str
    download_url: str
    associated_publications: list[str] = Field(default_factory=list)


class OpenNeuroDatasetSummary(BaseModel):
    """Lightweight summary used in search results."""
    accession_number: str
    title: str
    modalities: list[str]
    num_subjects: int
    tasks: list[str]


class OpenNeuroSearchResult(BaseModel):
    query: str
    modality: str | None
    total_returned: int
    datasets: list[OpenNeuroDatasetSummary]


class OpenNeuroFile(BaseModel):
    filename: str
    size: int
    directory: bool
    download_url: str | None


class OpenNeuroFileListing(BaseModel):
    accession_number: str
    snapshot_tag: str
    modality_filter: str | None
    files: list[OpenNeuroFile]


class OpenNeuroPublications(BaseModel):
    accession_number: str
    dataset_doi: str | None
    associated_paper_dois: list[str]
    references_and_links: list[str]


# =========================================================================
# Family B — NeuroVault
# =========================================================================

class SearchNeuroVaultCollectionsInput(BaseModel):
    query: str
    max_results: int = Field(default=20, ge=1, le=100)


class SearchNeuroVaultImagesInput(BaseModel):
    query: str = Field(description="Keyword(s) — matched against collection name/description/authors.")
    modality: str | None = Field(
        default=None,
        description="e.g. 'fMRI-BOLD', 'Diffusion MRI', 'Anatomical MRI'.",
    )
    map_type: str | None = Field(
        default=None,
        description="e.g. 'Z map', 'T map', 'F map', 'other', 'anatomical', 'parcellation'.",
    )
    max_results: int = Field(default=20, ge=1, le=100)


class GetNeuroVaultCollectionInput(BaseModel):
    collection_id: int


class GetNeuroVaultImageInput(BaseModel):
    image_id: int


class GetNeuroVaultCollectionPublicationsInput(BaseModel):
    collection_id: int


class NeuroVaultCollection(BaseModel):
    collection_id: int
    name: str
    description: str
    doi: str | None
    preprint_doi: str | None
    authors: str | None
    journal_name: str | None
    paper_url: str | None
    num_images: int
    download_url: str | None


class NeuroVaultCollectionSearchResult(BaseModel):
    query: str
    total_returned: int
    collections: list[NeuroVaultCollection]


class NeuroVaultImage(BaseModel):
    image_id: int
    name: str
    map_type: str | None
    modality: str | None
    collection_id: int
    file_url: str | None
    smoothness_fwhm: float | None
    analysis_level: str | None
    image_type: str | None
    is_thresholded: bool | None
    cognitive_paradigm: str | None


class NeuroVaultImageSearchResult(BaseModel):
    query: str
    modality: str | None
    map_type: str | None
    total_returned: int
    images: list[NeuroVaultImage]


class NeuroVaultCollectionPublications(BaseModel):
    collection_id: int
    doi: str | None
    preprint_doi: str | None
    paper_url: str | None
    journal_name: str | None
    authors: str | None


# =========================================================================
# Family C — PubMed
# =========================================================================

class SearchPubMedInput(BaseModel):
    query: str
    max_results: int = Field(default=20, ge=1, le=100)
    date_range_years: int | None = Field(
        default=None,
        description="If set, restrict to articles published within the last N years.",
        ge=1,
        le=50,
    )


class GetPubMedArticleInput(BaseModel):
    pmid: str


class GetPubMedAbstractInput(BaseModel):
    pmid: str


class FindRelatedPubMedInput(BaseModel):
    pmid: str
    max_results: int = Field(default=10, ge=1, le=50)


class PubMedArticle(BaseModel):
    pmid: str
    title: str
    authors: list[str]
    journal: str
    year: int | None
    abstract: str
    doi: str | None
    keywords: list[str] = Field(default_factory=list)
    mesh_terms: list[str] = Field(default_factory=list)


class PubMedAbstract(BaseModel):
    pmid: str
    title: str
    abstract: str


class PubMedSearchResult(BaseModel):
    query: str
    total_hits: int
    returned: int
    pmids: list[str]
    articles: list[PubMedArticle]


class PubMedRelatedResult(BaseModel):
    source_pmid: str
    related_pmids: list[str]
    articles: list[PubMedArticle]


# =========================================================================
# Family D — Bridge / cross-source
# =========================================================================

class FindPapersUsingDatasetInput(BaseModel):
    openneuro_accession: str


class FindNeuroVaultMapsForPaperInput(BaseModel):
    pmid: str


class FindDatasetsForTopicInput(BaseModel):
    research_topic: str
    modality: str | None = Field(
        default=None,
        description="Optional modality filter; 'mri', 'eeg', etc. for OpenNeuro.",
    )


class ComprehensiveLiteratureSearchInput(BaseModel):
    research_question: str
    modality: str | None = None


class CrossSourceResult(BaseModel):
    query: str
    pubmed_articles: list[PubMedArticle] = Field(default_factory=list)
    openneuro_datasets: list[OpenNeuroDatasetSummary] = Field(default_factory=list)
    neurovault_collections: list[NeuroVaultCollection] = Field(default_factory=list)
    suggested_next_queries: list[str] = Field(default_factory=list)
    notes: str | None = None
