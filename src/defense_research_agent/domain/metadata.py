"""Bibliographic metadata extraction contract.

Every extracted field keeps three things apart: the original surface text, the
normalized value, and the evidence that justifies it. Extractors must return
``None`` with a failure reason instead of guessing, because a fabricated author
or publication date is worse than a missing one for research provenance.
"""

from datetime import date
from enum import StrEnum

from pydantic import Field, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Confidence,
    DomainModel,
    EntityId,
    Label,
)
from defense_research_agent.domain.provenance import ExtractionProvenance


class MetadataField(StrEnum):
    """Bibliographic fields the extractor is expected to resolve."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    AUTHORS = "authors"
    ORGANIZATION = "organization"
    PUBLICATION_DATE = "publication_date"
    ISSUE_NUMBER = "issue_number"
    VOLUME = "volume"
    DOI = "doi"
    ABSTRACT = "abstract"
    KEYWORDS = "keywords"


class DatePrecision(StrEnum):
    """How precisely a publication date could be resolved."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"


class MetadataEvidence(DomainModel):
    """Where a normalized value came from, preserving the original surface text.

    ``page_number`` is the cover or body page the value was read from. Values
    inferred from the file name carry ``page_number=None`` and must be treated as
    weaker evidence than a cover page, per the filename-vs-cover rule in the plan.
    """

    raw_text: str
    page_number: PositiveInt | None = None

    @model_validator(mode="after")
    def raw_text_must_not_be_blank(self) -> "MetadataEvidence":
        """Reject evidence that records no original text."""
        if not self.raw_text.strip():
            raise ValueError("evidence raw_text must not be blank")
        return self


class ExtractedMetadataValue(DomainModel):
    """One extracted field with its evidence, or an explicit extraction failure."""

    field: MetadataField
    normalized: str | None = None
    evidence: MetadataEvidence | None = None
    confidence: Confidence = 0.0
    failure_reason: Label | None = None

    @model_validator(mode="after")
    def missing_value_requires_reason(self) -> "ExtractedMetadataValue":
        """Force an explicit reason instead of a silent ``None``."""
        if self.normalized is None and self.failure_reason is None:
            raise ValueError("missing metadata value must record a failure_reason")
        if self.normalized is not None and self.failure_reason is not None:
            raise ValueError("a resolved metadata value must not record a failure_reason")
        return self

    @model_validator(mode="after")
    def resolved_value_requires_evidence(self) -> "ExtractedMetadataValue":
        """Keep every resolved value traceable to its source text."""
        if self.normalized is not None and self.evidence is None:
            raise ValueError("a resolved metadata value must record evidence")
        return self


class ExtractedPublicationMetadata(DomainModel):
    """All metadata resolved for one publication by one extractor version."""

    publication_id: EntityId
    provenance: ExtractionProvenance
    values: list[ExtractedMetadataValue] = Field(default_factory=list)
    publication_date: date | None = None
    date_precision: DatePrecision | None = None

    @model_validator(mode="after")
    def fields_must_not_repeat(self) -> "ExtractedPublicationMetadata":
        """Reject two competing values for the same field."""
        seen = [value.field for value in self.values]
        if len(seen) != len(set(seen)):
            raise ValueError("each metadata field may appear at most once")
        return self

    @model_validator(mode="after")
    def date_and_precision_must_agree(self) -> "ExtractedPublicationMetadata":
        """Keep a resolved date and its precision declared together."""
        if (self.publication_date is None) != (self.date_precision is None):
            raise ValueError("publication_date and date_precision must be set together")
        return self
