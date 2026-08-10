"""Bibliographic metadata extraction contract.

Every extracted field keeps three things apart: the original surface text, the
normalized value, and the evidence that justifies it. Extractors must return
``None`` with a failure reason instead of guessing, because a fabricated author
or publication date is worse than a missing one for research provenance.

The shapes here follow the recommendations recorded in
``docs/DATA_QUALITY_REPORT.md``:

- dates from the file name, the document body, and the processing pipeline are
  three different facts and are stored separately, never overwritten into one
- a file-name year that disagrees with the body publication year is reported as
  a conflict rather than silently resolved
- authors are structured and repeatable, because real covers carry several
  authors with roles, affiliations, and footnote-linked institutions
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Confidence,
    DomainModel,
    EntityId,
    Label,
)
from defense_research_agent.domain.provenance import ExtractionProvenance

type PublicationYear = Annotated[int, Field(ge=1900, le=2100)]


class MetadataField(StrEnum):
    """Bibliographic fields the extractor is expected to resolve.

    ``AUTHORS`` is listed for completeness but is never stored in
    :attr:`ExtractedPublicationMetadata.values`; authors have a structured home
    of their own. Dates likewise live in :class:`PublicationDates`.
    """

    TITLE = "title"
    SUBTITLE = "subtitle"
    AUTHORS = "authors"
    ORGANIZATION = "organization"
    ISSUE_NUMBER = "issue_number"
    VOLUME = "volume"
    DOI = "doi"
    ABSTRACT = "abstract"
    KEYWORDS = "keywords"


MULTI_VALUED_METADATA_FIELDS = frozenset({MetadataField.KEYWORDS})
"""Fields that may legitimately appear more than once, distinguished by ordinal."""

STRUCTURED_METADATA_FIELDS = frozenset({MetadataField.AUTHORS})
"""Fields that have a dedicated model instead of a flat value."""


class DatePrecision(StrEnum):
    """How precisely a publication date could be resolved.

    ``SEASON`` exists because ``국방정책연구`` states issues as ``2024년 여름(40-2)``.
    Collapsing that to a month or a day would invent precision the source does
    not have.
    """

    DAY = "day"
    MONTH = "month"
    SEASON = "season"
    YEAR = "year"
    UNKNOWN = "unknown"


class MetadataEvidenceSource(StrEnum):
    """Where a value was read from, ordered from strongest to weakest."""

    COVER_PAGE = "cover_page"
    BODY = "body"
    FILENAME = "filename"
    PROCESSING_METADATA = "processing_metadata"


EVIDENCE_SOURCE_STRENGTH: dict[MetadataEvidenceSource, int] = {
    MetadataEvidenceSource.COVER_PAGE: 3,
    MetadataEvidenceSource.BODY: 2,
    MetadataEvidenceSource.FILENAME: 1,
    MetadataEvidenceSource.PROCESSING_METADATA: 0,
}
"""Precedence used when two sources disagree. Higher wins; ties are a conflict."""


class MetadataEvidence(DomainModel):
    """Where a normalized value came from, preserving the original surface text.

    Document-backed evidence must name the page it was read from. File-name and
    pipeline evidence has no page and is weaker by construction, which is what
    lets an extractor prefer a cover page over a truncated file name instead of
    merging the two.
    """

    source: MetadataEvidenceSource
    raw_text: str
    page_number: PositiveInt | None = None

    @property
    def strength(self) -> int:
        """Precedence of this evidence source."""
        return EVIDENCE_SOURCE_STRENGTH[self.source]

    @model_validator(mode="after")
    def raw_text_must_not_be_blank(self) -> "MetadataEvidence":
        """Reject evidence that records no original text."""
        if not self.raw_text.strip():
            raise ValueError("evidence raw_text must not be blank")
        return self

    @model_validator(mode="after")
    def page_number_must_match_source(self) -> "MetadataEvidence":
        """Tie page locators to sources that actually have pages."""
        document_backed = {
            MetadataEvidenceSource.COVER_PAGE,
            MetadataEvidenceSource.BODY,
        }
        if self.source in document_backed and self.page_number is None:
            raise ValueError(f"{self.source.value} evidence must record a page_number")
        if self.source not in document_backed and self.page_number is not None:
            raise ValueError(f"{self.source.value} evidence must not record a page_number")
        return self


class ExtractedMetadataValue(DomainModel):
    """One extracted flat field with its evidence, or an explicit failure."""

    field: MetadataField
    ordinal: NonNegativeInt = 0
    normalized: str | None = None
    evidence: MetadataEvidence | None = None
    confidence: Confidence = 0.0
    failure_reason: Label | None = None

    @model_validator(mode="after")
    def structured_fields_need_their_own_model(self) -> "ExtractedMetadataValue":
        """Keep authors out of the flat value list so structure is not lost."""
        if self.field in STRUCTURED_METADATA_FIELDS:
            raise ValueError(
                f"{self.field.value} must be recorded as a structured value, not a flat one"
            )
        return self

    @model_validator(mode="after")
    def only_multi_valued_fields_may_use_an_ordinal(self) -> "ExtractedMetadataValue":
        """Reject a second entry for a field that can only hold one value."""
        if self.ordinal > 0 and self.field not in MULTI_VALUED_METADATA_FIELDS:
            raise ValueError(f"{self.field.value} is single-valued and must use ordinal 0")
        return self

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


class ExtractedAuthor(DomainModel):
    """One author with the attributes real covers actually carry.

    ``국방정책연구`` links affiliations through ``*``/``**`` footnotes, so the
    affiliation is kept per author rather than once per publication.
    """

    ordinal: NonNegativeInt
    name: str | None = None
    role: Label | None = None
    affiliation: Label | None = None
    email: str | None = None
    is_primary: bool = False
    evidence: MetadataEvidence | None = None
    confidence: Confidence = 0.0
    failure_reason: Label | None = None

    @model_validator(mode="after")
    def missing_name_requires_reason(self) -> "ExtractedAuthor":
        """Force an explicit reason instead of an unnamed author."""
        if self.name is None and self.failure_reason is None:
            raise ValueError("an author without a name must record a failure_reason")
        if self.name is not None and self.failure_reason is not None:
            raise ValueError("a resolved author must not record a failure_reason")
        return self

    @model_validator(mode="after")
    def resolved_author_requires_evidence(self) -> "ExtractedAuthor":
        """Keep every resolved author traceable to its source text."""
        if self.name is not None and self.evidence is None:
            raise ValueError("a resolved author must record evidence")
        return self


class PublicationDates(DomainModel):
    """The three publication-related dates, kept apart on purpose.

    ``filename_year`` is the file classification year, ``published_at`` is the
    date stated in the document, and ``processed_at`` is when the ingestion
    pipeline read the file. They routinely disagree, and collapsing them loses
    the disagreement that a reviewer needs to see.
    """

    filename_year: PublicationYear | None = None
    published_at: date | None = None
    published_precision: DatePrecision | None = None
    issue_label: Label | None = None
    processed_at: datetime | None = None
    date_evidence: MetadataEvidence | None = None
    failure_reason: Label | None = None

    @property
    def has_year_conflict(self) -> bool:
        """Whether the file-name year disagrees with the stated publication year.

        Derived rather than stored so it can never drift from the values it
        describes. A conflict is a warning for human review, not a licence to
        overwrite either value.
        """
        if self.filename_year is None or self.published_at is None:
            return False
        return self.filename_year != self.published_at.year

    @model_validator(mode="after")
    def date_and_precision_must_agree(self) -> "PublicationDates":
        """Keep a resolved date and its precision declared together."""
        if (self.published_at is None) != (self.published_precision is None):
            raise ValueError("published_at and published_precision must be set together")
        return self

    @model_validator(mode="after")
    def resolved_date_or_failure_reason_must_be_set(self) -> "PublicationDates":
        """Require either a traceable publication date or an explicit failure."""
        if self.published_at is None and self.failure_reason is None:
            raise ValueError("a missing published_at must record a failure_reason")
        if self.published_at is not None and self.failure_reason is not None:
            raise ValueError("a resolved published_at must not record a failure_reason")
        return self

    @model_validator(mode="after")
    def date_evidence_must_match_resolution(self) -> "PublicationDates":
        """Keep evidence only for a resolved publication date."""
        if self.published_at is not None and self.date_evidence is None:
            raise ValueError("a resolved published_at must record date_evidence")
        if self.published_at is None and self.date_evidence is not None:
            raise ValueError("an unresolved published_at must not record date_evidence")
        return self

    @model_validator(mode="after")
    def season_precision_requires_issue_label(self) -> "PublicationDates":
        """Preserve the original seasonal wording behind a season-precision date."""
        if self.published_precision is DatePrecision.SEASON and self.issue_label is None:
            raise ValueError("season precision must record the original issue_label")
        return self


class ExtractedPublicationMetadata(DomainModel):
    """All metadata resolved for one publication by one extractor version."""

    publication_id: EntityId
    provenance: ExtractionProvenance
    values: list[ExtractedMetadataValue] = Field(default_factory=list)
    authors: list[ExtractedAuthor] = Field(default_factory=list)
    dates: PublicationDates = Field(
        default_factory=lambda: PublicationDates(failure_reason="발행일이 제공되지 않음")
    )

    @model_validator(mode="after")
    def field_and_ordinal_must_be_unique(self) -> "ExtractedPublicationMetadata":
        """Reject two competing values for the same field position."""
        seen = [(value.field, value.ordinal) for value in self.values]
        if len(seen) != len(set(seen)):
            raise ValueError("each (field, ordinal) pair may appear at most once")
        return self

    @model_validator(mode="after")
    def author_ordinals_must_be_unique(self) -> "ExtractedPublicationMetadata":
        """Keep author order stable and unambiguous."""
        ordinals = [author.ordinal for author in self.authors]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("author ordinal must not repeat")
        return self

    @model_validator(mode="after")
    def at_most_one_primary_author(self) -> "ExtractedPublicationMetadata":
        """Reject two authors both claiming to be the representative author."""
        if sum(1 for author in self.authors if author.is_primary) > 1:
            raise ValueError("at most one author may be marked primary")
        return self
