"""Validated query, result, and aggregation models for publication search."""

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import Field, NonNegativeInt, model_validator

from defense_research_agent.domain.common import DomainModel, Label
from defense_research_agent.domain.publication import PublicationType, ResearchPublication

type SearchScore = Annotated[float, Field(ge=0.0)]


class SearchField(StrEnum):
    """Searchable bibliography and content fields."""

    TITLE = "title"
    ABSTRACT = "abstract"
    KEYWORDS = "keywords"
    CONTENT = "content"


class PublicationSearchFilters(DomainModel):
    """Optional deterministic filters applied before ranking."""

    start_date: date | None = None
    end_date: date | None = None
    publication_types: list[PublicationType] = Field(default_factory=list)
    authors: list[Label] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_date_range(self) -> "PublicationSearchFilters":
        """Reject a reversed date interval."""
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date must be on or before end_date")
        return self


class PublicationSearchResult(DomainModel):
    """A ranked publication with transparent lexical match information."""

    publication: ResearchPublication
    score: SearchScore
    matched_fields: list[SearchField] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)


class PublicationDistribution(DomainModel):
    """Publication counts for records whose effective year is known."""

    total: NonNegativeInt
    by_year: dict[int, NonNegativeInt] = Field(default_factory=dict)
    by_publication_type: dict[str, NonNegativeInt] = Field(default_factory=dict)
    unknown_date_count: NonNegativeInt
