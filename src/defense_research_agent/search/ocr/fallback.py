"""Deterministic policy and orchestration boundary for page-level OCR fallback."""

from collections.abc import Sequence
from enum import StrEnum
from fractions import Fraction

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Checksum,
    Confidence,
    DomainModel,
    Label,
)
from defense_research_agent.domain.provenance import ExtractionProvenance
from defense_research_agent.domain.publication import PublicationPage
from defense_research_agent.domain.quality import PublicationQualityStatus
from defense_research_agent.search.ocr.base import OcrPageInput, OcrPageResult, OcrProvider
from defense_research_agent.search.parsers.base import (
    ParserErrorCode,
    ParseResult,
    ParserFailure,
)

DEFAULT_MINIMUM_CONFIDENCE = 0.5
DEFAULT_MINIMUM_PRINTABLE_RATIO = 0.95
OCR_FALLBACK_POLICY_VERSION = "ocr-fallback-v1"

OCR_REMEDIATION_STATUSES = frozenset(
    {
        PublicationQualityStatus.LOW_TEXT,
        PublicationQualityStatus.CORRUPT_TEXT,
        PublicationQualityStatus.ORPHAN_PDF,
    }
)
"""Quality statuses whose artifact queue already requests OCR remediation."""

_ALLOWED_LAYOUT_CONTROLS = frozenset({"\n", "\t", "\r"})
_REPLACEMENT_CHARACTER = "\ufffd"


class OcrNeedReason(StrEnum):
    """Evidence that permits an OCR attempt for one page."""

    PARSER_REQUIRES_OCR = "parser_requires_ocr"
    QUALITY_REMEDIATION_STATUS = "quality_remediation_status"


class OcrDecisionCode(StrEnum):
    """Stable reason an OCR page was skipped, rejected, failed, or adopted."""

    NOT_REQUIRED = "not_required"
    MISSING_PAGE_INPUT = "missing_page_input"
    PROVIDER_FAILURE = "provider_failure"
    BELOW_MINIMUM_CONFIDENCE = "below_minimum_confidence"
    BELOW_MINIMUM_PRINTABLE_RATIO = "below_minimum_printable_ratio"
    NOT_BETTER_THAN_BASE = "not_better_than_base"
    ADOPTED = "adopted"


class OcrTextQuality(DomainModel):
    """Deterministic observations used to compare base and OCR page text."""

    character_count: NonNegativeInt
    acceptable_character_count: NonNegativeInt
    usable_character_count: NonNegativeInt
    suspicious_character_count: NonNegativeInt
    printable_ratio: Confidence

    @model_validator(mode="after")
    def counts_must_describe_the_same_text(self) -> "OcrTextQuality":
        """Reject metrics that cannot have come from one character sequence."""
        if self.acceptable_character_count + self.suspicious_character_count != (
            self.character_count
        ):
            raise ValueError("acceptable and suspicious counts must sum to character_count")
        if self.usable_character_count > self.acceptable_character_count:
            raise ValueError("usable_character_count must not exceed acceptable count")
        return self


class OcrPageDecision(DomainModel):
    """Complete evidence and disposition for one possible page OCR attempt."""

    page_number: PositiveInt
    input_checksum: Checksum | None = None
    need_reasons: list[OcrNeedReason] = Field(default_factory=list)
    decision: OcrDecisionCode
    reason: Label
    baseline_quality: OcrTextQuality
    ocr_quality: OcrTextQuality | None = None
    ocr_result: OcrPageResult | None = None

    @property
    def attempted(self) -> bool:
        """Whether the provider received this page."""
        return self.ocr_result is not None

    @property
    def adopted(self) -> bool:
        """Whether the OCR text replaced or filled the base page text."""
        return self.decision is OcrDecisionCode.ADOPTED

    @model_validator(mode="after")
    def evidence_must_match_decision(self) -> "OcrPageDecision":
        """Keep audit evidence complete for every decision code."""
        if self.decision is OcrDecisionCode.NOT_REQUIRED:
            if self.need_reasons:
                raise ValueError("not-required decision must not carry need reasons")
            if self.input_checksum is None:
                raise ValueError("not-required input must preserve its checksum")
        elif not self.need_reasons:
            raise ValueError("OCR-required decision must record at least one need reason")

        no_attempt = {
            OcrDecisionCode.NOT_REQUIRED,
            OcrDecisionCode.MISSING_PAGE_INPUT,
        }
        if self.decision in no_attempt:
            if self.ocr_result is not None or self.ocr_quality is not None:
                raise ValueError("non-attempt decision must not carry an OCR result")
        elif self.ocr_result is None:
            raise ValueError("attempt decision must preserve its OCR result")

        if self.decision is OcrDecisionCode.MISSING_PAGE_INPUT:
            if self.input_checksum is not None:
                raise ValueError("missing-input decision must not carry a checksum")
        elif self.input_checksum is None:
            raise ValueError("supplied page input must preserve its checksum")

        successful_decisions = {
            OcrDecisionCode.BELOW_MINIMUM_CONFIDENCE,
            OcrDecisionCode.BELOW_MINIMUM_PRINTABLE_RATIO,
            OcrDecisionCode.NOT_BETTER_THAN_BASE,
            OcrDecisionCode.ADOPTED,
        }
        if self.decision in successful_decisions and self.ocr_quality is None:
            raise ValueError("successful OCR result must preserve output quality")
        if self.decision in successful_decisions and (
            self.ocr_result is None or not self.ocr_result.is_success
        ):
            raise ValueError("successful decision must preserve a successful OCR result")
        if self.decision is OcrDecisionCode.PROVIDER_FAILURE:
            if self.ocr_quality is not None:
                raise ValueError("failed OCR result must not carry output quality")
            if self.ocr_result is None or self.ocr_result.is_success:
                raise ValueError("provider-failure decision must preserve a failed OCR result")
        if self.ocr_result is not None:
            if self.ocr_result.page_number != self.page_number:
                raise ValueError("OCR result page_number must match its decision")
            if self.ocr_result.input_checksum != self.input_checksum:
                raise ValueError("OCR result checksum must match its decision")
        return self


class OcrFallbackResult(DomainModel):
    """Final mixed-origin pages plus every OCR decision and original failure."""

    policy_version: Label
    minimum_confidence: Confidence
    minimum_printable_ratio: Confidence
    quality_status: PublicationQualityStatus | None = None
    base_provenance: ExtractionProvenance
    pages: list[PublicationPage] = Field(default_factory=list)
    parser_failures: list[ParserFailure] = Field(default_factory=list)
    decisions: list[OcrPageDecision] = Field(default_factory=list)
    document_eligible: bool
    requires_ocr: bool

    @model_validator(mode="after")
    def pages_and_decisions_must_be_ordered(self) -> "OcrFallbackResult":
        """Keep page locators deterministic and unambiguous."""
        for values, name in ((self.pages, "pages"), (self.decisions, "decisions")):
            numbers = [value.page_number for value in values]
            if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
                raise ValueError(f"{name} must have unique ascending page numbers")
        return self


class OcrFallbackPolicy:
    """Conservative, versioned OCR eligibility and strict-improvement policy.

    Parser-driven attempts require both the document-level ``requires_ocr``
    signal and a page-scoped ``EMPTY_PAGE`` failure. The current parser cannot
    identify which of several empty pages supplied the scan signal, so every
    empty page in that signalled document is eligible; the adoption gate keeps
    blank or poor OCR from replacing anything.

    The quality gate's ``low_text``, ``corrupt_text``, and ``orphan_pdf`` states
    also permit attempts because those exact states are emitted to the existing
    re-extraction/OCR remediation queue. Other statuses do not independently
    permit OCR. A remediation status applies to every known/rendered page so a
    corrupt existing text page can be compared with OCR, not only filled when
    absent.

    Successful OCR must meet both configured floors. Any usable OCR is better
    than a base page with no usable text. Otherwise it is better only when its
    exact printable ratio is higher than the base page's, or when the ratios are
    equal and it contains strictly more usable non-whitespace characters. This
    lexicographic rule is deliberately conservative: more text cannot compensate
    for a worse corruption ratio in an already non-empty base page, and equality
    is never adopted.
    """

    def __init__(
        self,
        *,
        minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
        minimum_printable_ratio: float = DEFAULT_MINIMUM_PRINTABLE_RATIO,
    ) -> None:
        self._minimum_confidence = self._confidence(
            minimum_confidence,
            name="minimum_confidence",
        )
        self._minimum_printable_ratio = self._confidence(
            minimum_printable_ratio,
            name="minimum_printable_ratio",
        )
        self._minimum_printable_fraction = Fraction(str(self._minimum_printable_ratio))

    @staticmethod
    def _confidence(value: float, *, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a float")
        resolved = float(value)
        if not 0.0 <= resolved <= 1.0:
            raise ValueError(f"{name} must be between zero and one")
        return resolved

    @property
    def version(self) -> str:
        """Return the version of eligibility, metrics, and comparison behavior."""
        return OCR_FALLBACK_POLICY_VERSION

    @property
    def minimum_confidence(self) -> float:
        """Lowest provider confidence eligible for adoption."""
        return self._minimum_confidence

    @property
    def minimum_printable_ratio(self) -> float:
        """Lowest acceptable-character ratio eligible for adoption."""
        return self._minimum_printable_ratio

    def document_requires_ocr(
        self,
        parse_result: ParseResult,
        quality_status: PublicationQualityStatus | None = None,
    ) -> bool:
        """Whether parser evidence or the remediation queue permits OCR."""
        return parse_result.requires_ocr or quality_status in OCR_REMEDIATION_STATUSES

    def page_requires_ocr(
        self,
        parse_result: ParseResult,
        page_number: int,
        quality_status: PublicationQualityStatus | None = None,
    ) -> bool:
        """Whether a particular page is eligible under the documented rules."""
        return bool(self.need_reasons(parse_result, page_number, quality_status))

    def need_reasons(
        self,
        parse_result: ParseResult,
        page_number: int,
        quality_status: PublicationQualityStatus | None = None,
    ) -> tuple[OcrNeedReason, ...]:
        """Return all evidence permitting OCR in a stable order."""
        reasons: list[OcrNeedReason] = []
        empty_page_numbers = {
            failure.page_number
            for failure in parse_result.failures
            if failure.code is ParserErrorCode.EMPTY_PAGE and failure.page_number is not None
        }
        if parse_result.requires_ocr and page_number in empty_page_numbers:
            reasons.append(OcrNeedReason.PARSER_REQUIRES_OCR)
        if quality_status in OCR_REMEDIATION_STATUSES:
            reasons.append(OcrNeedReason.QUALITY_REMEDIATION_STATUS)
        return tuple(reasons)

    def measure_text(self, text: str) -> OcrTextQuality:
        """Measure text exactly, without stripping or Unicode normalization."""
        acceptable_count = 0
        usable_count = 0
        suspicious_count = 0
        for character in text:
            acceptable = character != _REPLACEMENT_CHARACTER and (
                character.isprintable() or character in _ALLOWED_LAYOUT_CONTROLS
            )
            if acceptable:
                acceptable_count += 1
                if not character.isspace():
                    usable_count += 1
            else:
                suspicious_count += 1
        character_count = len(text)
        printable_ratio = acceptable_count / character_count if character_count else 1.0
        return OcrTextQuality(
            character_count=character_count,
            acceptable_character_count=acceptable_count,
            usable_character_count=usable_count,
            suspicious_character_count=suspicious_count,
            printable_ratio=printable_ratio,
        )

    def adoption_decision(
        self,
        baseline_quality: OcrTextQuality,
        ocr_result: OcrPageResult,
    ) -> tuple[OcrDecisionCode, OcrTextQuality | None, str]:
        """Return a deterministic decision, measurements, and operator rationale."""
        if not ocr_result.is_success:
            return (
                OcrDecisionCode.PROVIDER_FAILURE,
                None,
                "OCR provider returned a page-scoped failure",
            )
        if ocr_result.text is None or ocr_result.confidence is None:  # pragma: no cover
            raise AssertionError("validated successful OCR result has no output")

        ocr_quality = self.measure_text(ocr_result.text)
        if ocr_result.confidence < self.minimum_confidence:
            return (
                OcrDecisionCode.BELOW_MINIMUM_CONFIDENCE,
                ocr_quality,
                "OCR confidence is below the policy minimum",
            )
        if not self._meets_printable_floor(ocr_quality):
            return (
                OcrDecisionCode.BELOW_MINIMUM_PRINTABLE_RATIO,
                ocr_quality,
                "OCR text is blank or below the printable-ratio minimum",
            )
        if not self._is_strictly_better(ocr_quality, baseline_quality):
            return (
                OcrDecisionCode.NOT_BETTER_THAN_BASE,
                ocr_quality,
                "OCR quality is worse than or equal to base extraction",
            )
        return (
            OcrDecisionCode.ADOPTED,
            ocr_quality,
            "OCR quality is a strict improvement over base extraction",
        )

    def _meets_printable_floor(self, quality: OcrTextQuality) -> bool:
        if quality.usable_character_count == 0 or quality.character_count == 0:
            return False
        ratio = Fraction(quality.acceptable_character_count, quality.character_count)
        return ratio >= self._minimum_printable_fraction

    @staticmethod
    def _is_strictly_better(
        candidate: OcrTextQuality,
        baseline: OcrTextQuality,
    ) -> bool:
        if baseline.usable_character_count == 0:
            return candidate.usable_character_count > 0
        candidate_fraction = OcrFallbackPolicy._printable_fraction(candidate)
        baseline_fraction = OcrFallbackPolicy._printable_fraction(baseline)
        if candidate_fraction != baseline_fraction:
            return candidate_fraction > baseline_fraction
        return candidate.usable_character_count > baseline.usable_character_count

    @staticmethod
    def _printable_fraction(quality: OcrTextQuality) -> Fraction:
        if quality.character_count == 0:
            return Fraction(1, 1)
        return Fraction(quality.acceptable_character_count, quality.character_count)


class OcrFallbackBoundary:
    """Run eligible pages independently and merge only adopted OCR text."""

    def __init__(
        self,
        provider: OcrProvider,
        policy: OcrFallbackPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy or OcrFallbackPolicy()

    @property
    def provider(self) -> OcrProvider:
        """Return the provider used for page attempts."""
        return self._provider

    @property
    def policy(self) -> OcrFallbackPolicy:
        """Return the deterministic policy used for eligibility and adoption."""
        return self._policy

    def apply(
        self,
        parse_result: ParseResult,
        page_inputs: Sequence[OcrPageInput],
        *,
        quality_status: PublicationQualityStatus | None = None,
    ) -> OcrFallbackResult:
        """Attempt every eligible page without allowing one failure to stop another."""
        inputs_by_page = self._index_inputs(page_inputs)
        pages_by_number = {
            page.page_number: page.model_copy(deep=True) for page in parse_result.pages
        }
        candidate_numbers = self._candidate_page_numbers(
            parse_result,
            inputs_by_page,
            quality_status,
        )
        decision_numbers = sorted(set(inputs_by_page) | candidate_numbers)
        decisions: list[OcrPageDecision] = []

        for page_number in decision_numbers:
            baseline_page = pages_by_number.get(page_number)
            baseline_text = "" if baseline_page is None else baseline_page.text
            baseline_quality = self.policy.measure_text(baseline_text)
            need_reasons = list(self.policy.need_reasons(parse_result, page_number, quality_status))
            page_input = inputs_by_page.get(page_number)

            if not need_reasons:
                if page_input is None:  # pragma: no cover - excluded from decision_numbers
                    raise AssertionError("non-candidate page has no supplied input")
                decisions.append(
                    OcrPageDecision(
                        page_number=page_number,
                        input_checksum=page_input.input_checksum,
                        decision=OcrDecisionCode.NOT_REQUIRED,
                        reason="page has no parser signal or OCR-remediation quality status",
                        baseline_quality=baseline_quality,
                    )
                )
                continue

            if page_input is None:
                decisions.append(
                    OcrPageDecision(
                        page_number=page_number,
                        need_reasons=need_reasons,
                        decision=OcrDecisionCode.MISSING_PAGE_INPUT,
                        reason="eligible page has no rendered input bytes",
                        baseline_quality=baseline_quality,
                    )
                )
                continue

            ocr_result = self.provider.recognize_page(page_input)
            self._validate_provider_result(page_input, ocr_result)
            decision_code, ocr_quality, reason = self.policy.adoption_decision(
                baseline_quality,
                ocr_result,
            )
            decisions.append(
                OcrPageDecision(
                    page_number=page_number,
                    input_checksum=page_input.input_checksum,
                    need_reasons=need_reasons,
                    decision=decision_code,
                    reason=reason,
                    baseline_quality=baseline_quality,
                    ocr_quality=ocr_quality,
                    ocr_result=ocr_result,
                )
            )
            if decision_code is OcrDecisionCode.ADOPTED:
                if ocr_result.text is None:  # pragma: no cover
                    raise AssertionError("adopted OCR result has no text")
                pages_by_number[page_number] = PublicationPage(
                    page_number=page_number,
                    text=ocr_result.text,
                    provenance=ExtractionProvenance(
                        parser_name=ocr_result.provider_name,
                        parser_version=ocr_result.provider_version,
                        source_checksum=parse_result.provenance.source_checksum,
                    ),
                    section_title=(None if baseline_page is None else baseline_page.section_title),
                )

        document_eligible = self.policy.document_requires_ocr(parse_result, quality_status)
        required_decisions = [decision for decision in decisions if decision.need_reasons]
        requires_ocr = document_eligible and (
            not required_decisions or any(not decision.adopted for decision in required_decisions)
        )
        return OcrFallbackResult(
            policy_version=self.policy.version,
            minimum_confidence=self.policy.minimum_confidence,
            minimum_printable_ratio=self.policy.minimum_printable_ratio,
            quality_status=quality_status,
            base_provenance=parse_result.provenance,
            pages=[pages_by_number[number] for number in sorted(pages_by_number)],
            parser_failures=[failure.model_copy(deep=True) for failure in parse_result.failures],
            decisions=decisions,
            document_eligible=document_eligible,
            requires_ocr=requires_ocr,
        )

    @staticmethod
    def _index_inputs(page_inputs: Sequence[OcrPageInput]) -> dict[int, OcrPageInput]:
        indexed: dict[int, OcrPageInput] = {}
        for page_input in page_inputs:
            if page_input.page_number in indexed:
                raise ValueError("page_inputs must not repeat a page_number")
            indexed[page_input.page_number] = page_input
        return indexed

    def _candidate_page_numbers(
        self,
        parse_result: ParseResult,
        inputs_by_page: dict[int, OcrPageInput],
        quality_status: PublicationQualityStatus | None,
    ) -> set[int]:
        candidates = {
            failure.page_number
            for failure in parse_result.failures
            if failure.page_number is not None
            and self.policy.page_requires_ocr(
                parse_result,
                failure.page_number,
                quality_status,
            )
        }
        if quality_status in OCR_REMEDIATION_STATUSES:
            candidates.update(page.page_number for page in parse_result.pages)
            candidates.update(inputs_by_page)
        return candidates

    def _validate_provider_result(
        self,
        page_input: OcrPageInput,
        result: OcrPageResult,
    ) -> None:
        if result.page_number != page_input.page_number:
            raise ValueError("OCR provider result page_number does not match input")
        if result.input_checksum != page_input.input_checksum:
            raise ValueError("OCR provider result checksum does not match input")
        if result.provider_name != self.provider.name:
            raise ValueError("OCR provider result name does not match provider")
        if result.provider_version != self.provider.version:
            raise ValueError("OCR provider result version does not match provider")
