"""Corpus admission quality contract for publications.

The quality gate decides which publications may enter the default index. Every
verdict must be reproducible from the measurements and a versioned threshold set,
so the decision can be replayed after a parser or threshold change.
"""

from enum import StrEnum

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Confidence,
    DomainModel,
    EntityId,
    Label,
)


class PublicationQualityStatus(StrEnum):
    """Admission status assigned by the quality gate."""

    READY = "ready"
    WARNING = "warning"
    LOW_TEXT = "low_text"
    CORRUPT_TEXT = "corrupt_text"
    DUPLICATE = "duplicate"
    ORPHAN_PDF = "orphan_pdf"
    MANUAL_REVIEW = "manual_review"

    @property
    def is_indexable(self) -> bool:
        """Whether a publication with this status may enter the default index."""
        return self in {PublicationQualityStatus.READY, PublicationQualityStatus.WARNING}


class QualityMeasurements(DomainModel):
    """Deterministic text measurements computed from extracted pages.

    These are observations only. No threshold is applied here so the same
    measurements can be re-judged under a different threshold version.
    """

    character_count: NonNegativeInt
    page_count: NonNegativeInt
    non_empty_page_count: NonNegativeInt
    control_character_count: NonNegativeInt
    printable_ratio: Confidence
    korean_ratio: Confidence

    @property
    def mean_characters_per_page(self) -> float:
        """Page density. Zero for a publication with no pages."""
        if self.page_count == 0:
            return 0.0
        return self.character_count / self.page_count

    @property
    def control_character_ratio(self) -> float:
        """Share of extracted characters that are C0/C1 control characters."""
        if self.character_count == 0:
            return 0.0
        return self.control_character_count / self.character_count

    @property
    def non_empty_page_ratio(self) -> float:
        """Share of pages that carry any text."""
        if self.page_count == 0:
            return 0.0
        return self.non_empty_page_count / self.page_count

    @model_validator(mode="after")
    def non_empty_pages_must_not_exceed_pages(self) -> "QualityMeasurements":
        """Reject measurements whose non-empty page count exceeds total pages."""
        if self.non_empty_page_count > self.page_count:
            raise ValueError("non_empty_page_count must not exceed page_count")
        return self


class QualityThresholds(DomainModel):
    """Versioned admission thresholds.

    ``thresholds_version`` is recorded on every verdict. Changing any threshold
    requires a new version so previously stored verdicts stay interpretable.

    The defaults below are provisional and must not be treated as calibrated.
    Only ``min_character_count`` has a corpus basis (``DATA_QUALITY_REPORT.md``
    DQ-02: 38 of 370 documents fall under 1,000 characters). The ratio defaults
    are placeholders: DQ-03 records that 192 of 370 documents carry C0/C1
    control characters, many using ``U+0001`` as a visual space, so whether such
    characters are counted or normalized away changes the outcome for a majority
    of the corpus. Calibrate against the corpus and record the expected
    exclusion count before using these in a real admission run.
    """

    thresholds_version: Label
    min_character_count: NonNegativeInt = 1_000
    min_non_empty_page_ratio: Confidence = 0.5
    min_printable_ratio: Confidence = 0.9
    min_korean_ratio: Confidence = 0.1
    max_control_character_ratio: Confidence = 0.01


class PublicationQualityVerdict(DomainModel):
    """Reproducible admission decision for one publication."""

    publication_id: EntityId
    status: PublicationQualityStatus
    measurements: QualityMeasurements
    thresholds_version: Label
    reasons: list[Label] = Field(default_factory=list)
    duplicate_of: EntityId | None = None
    manual_review_page: PositiveInt | None = None

    @model_validator(mode="after")
    def non_ready_status_requires_reason(self) -> "PublicationQualityVerdict":
        """Force an explicit reason whenever a publication is not plainly ready."""
        if self.status is not PublicationQualityStatus.READY and not self.reasons:
            raise ValueError("non-ready verdict must record at least one reason")
        return self

    @model_validator(mode="after")
    def duplicate_status_requires_target(self) -> "PublicationQualityVerdict":
        """Keep ``duplicate`` verdicts traceable to the publication they duplicate."""
        is_duplicate = self.status is PublicationQualityStatus.DUPLICATE
        if is_duplicate and self.duplicate_of is None:
            raise ValueError("duplicate verdict must record duplicate_of")
        if not is_duplicate and self.duplicate_of is not None:
            raise ValueError("duplicate_of is only valid for a duplicate verdict")
        return self
