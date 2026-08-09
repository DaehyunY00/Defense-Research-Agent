"""Corpus admission quality contract for publications.

The quality gate decides which publications may enter the default index. Every
verdict must be reproducible from the measurements and a versioned threshold set,
so the decision can be replayed after a parser or threshold change.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Confidence,
    DomainModel,
    EntityId,
    Label,
)

DEFAULT_QUALITY_THRESHOLDS_VERSION = "quality-v1-corpus370"
"""Version of the threshold set calibrated against the 370-document corpus."""

CONTROL_CHARACTER_SUBSTITUTIONS: Mapping[str, str] = MappingProxyType({"\x01": " "})
"""Control characters replaced before measurement, and why.

``U+0001`` is a PDF extraction artifact standing in for a space. Measured over
the corpus it appears in 163 of the 192 documents that carry control characters,
while every other C0 code except ``U+0007`` appears in exactly one document.
Counting it as corruption would exclude 78 of 100 ``국방논단`` at a 1% control
threshold; replacing it with a space first leaves exactly one excluded document,
the genuinely corrupt report DQ-03 identifies. See
``scripts/measure_quality_thresholds.py`` and ``docs/DECISIONS.md``.

Implementations of the quality gate must apply these substitutions before
counting control characters, and must not apply them to stored page text.
"""


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

    The defaults are calibrated against the 370-document corpus by
    ``scripts/measure_quality_thresholds.py``. Together they flag 39 of 370
    documents: 38 for low text, and exactly one real publication for corruption
    — the report DQ-03 identifies, whose body is 39.9% control characters. Each
    threshold sits in a flat region of the measured distribution, so a small
    calibration error does not swing the outcome.

    Re-run the measurement and bump ``thresholds_version`` whenever the parser
    changes, because every number here describes extracted text, not the PDFs.
    """

    thresholds_version: Label

    min_character_count: NonNegativeInt = 1_000
    """DQ-02. Excludes 38 of 370, matching the report's own count."""

    min_non_empty_page_ratio: Confidence = 0.25
    """Excludes 3. At 0.5 it would exclude 28, mostly four-page ``Brief`` issues
    where a single blank page crosses the line; their real defect is low text,
    which ``min_character_count`` already catches. Measured p10 is 0.75."""

    min_printable_ratio: Confidence = 0.95
    """Share of characters that are printable after
    :data:`CONTROL_CHARACTER_SUBSTITUTIONS`, judged by ``str.isprintable()`` plus
    newline, tab, and carriage return. This is deliberately stricter than "not a
    control character" so format and unassigned characters are also caught.
    Excludes 2. Measured p10 is 0.9977; 0.99 would exclude 8 including 5
    ``국방정책연구``."""

    min_korean_ratio: Confidence = 0.1
    """Excludes 2: the corrupt report and a non-publication index file. The
    lowest legitimate ``국방정책연구`` measures 0.338, so the English
    ``Abstract``/``Keywords`` sections present in all 59 issues stay well clear.
    0.2 would wrongly exclude a 279k-character AI research report at 0.152."""

    max_control_character_ratio: Confidence = 0.01
    """Applied after :data:`CONTROL_CHARACTER_SUBSTITUTIONS`. Excludes exactly 1
    document, measured at 0.399. The highest legitimate document measures below
    0.005, so this sits with a 2x margin above real text and 40x below the
    corrupt one. Without the substitution the same threshold would exclude 86
    documents including 78 of 100 ``국방논단``."""


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
