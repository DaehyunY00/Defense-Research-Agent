"""Validated ingestion results and reporting models."""

from pydantic import Field, NonNegativeInt

from defense_research_agent.domain.common import DomainModel


class IngestionFailure(DomainModel):
    """A source file that could not be read without stopping the whole run."""

    path: str
    reader: str | None = None
    error_type: str
    reason: str


class IngestionReport(DomainModel):
    """Deterministic summary of one read-only dataset ingestion run."""

    input_path: str
    publications_path: str
    total_file_count: NonNegativeInt
    success_count: NonNegativeInt
    failure_count: NonNegativeInt
    skipped_count: NonNegativeInt
    publication_count: NonNegativeInt
    publication_type_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    suspected_duplicate_count: NonNegativeInt
    suspected_duplicate_group_count: NonNegativeInt
    missing_field_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    failures: list[IngestionFailure] = Field(default_factory=list)
