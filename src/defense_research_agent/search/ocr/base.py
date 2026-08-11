"""Page-level OCR provider contract.

OCR adapters receive already-rendered page bytes. Rendering a PDF page is kept
outside this contract because renderer selection and image settings affect the
bytes sent to an OCR engine and therefore require their own versioned decision.

Expected provider failures are returned as :class:`OcrPageFailure` values. A
timeout or provider error on one page must not force callers to discard results
from other pages. Provider raw responses, credentials, and source contents must
not appear in failure messages.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from hashlib import sha256

from pydantic import Field, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Checksum,
    Confidence,
    DomainModel,
    Label,
)


class OcrErrorCode(StrEnum):
    """Stable expected-failure taxonomy shared by OCR adapters."""

    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"


class OcrPageInput(DomainModel):
    """Exact rendered bytes for one source page and their SHA-256 checksum."""

    page_number: PositiveInt
    image_bytes: bytes = Field(repr=False, exclude=True)
    input_checksum: Checksum

    @model_validator(mode="after")
    def checksum_must_match_image_bytes(self) -> "OcrPageInput":
        """Prevent OCR results from being attributed to different page bytes."""
        actual_checksum = sha256(self.image_bytes).hexdigest()
        if actual_checksum != self.input_checksum:
            raise ValueError("input_checksum does not match image_bytes")
        return self


class OcrPageFailure(DomainModel):
    """One recoverable OCR failure for the page in the enclosing result."""

    code: OcrErrorCode
    message: Label


class OcrPageResult(DomainModel):
    """Outcome of one OCR attempt, including exact input and provider lineage.

    A success preserves the OCR provider's original text without stripping or
    normalization. A failure carries neither text nor confidence. Provider
    identity and the rendered-page checksum are present in both cases so every
    attempt remains auditable.
    """

    page_number: PositiveInt
    input_checksum: Checksum
    provider_name: Label
    provider_version: Label
    text: str | None = None
    confidence: Confidence | None = None
    failure: OcrPageFailure | None = None

    @property
    def is_success(self) -> bool:
        """Whether the provider returned text and confidence for this page."""
        return self.failure is None

    @model_validator(mode="after")
    def success_and_failure_fields_must_not_mix(self) -> "OcrPageResult":
        """Require exactly one of the success and failure result shapes."""
        if self.failure is None:
            if self.text is None or self.confidence is None:
                raise ValueError("successful OCR result requires text and confidence")
        elif self.text is not None or self.confidence is not None:
            raise ValueError("failed OCR result must not include text or confidence")
        return self


class OcrProvider(ABC):
    """Interface implemented by deterministic fakes and future OCR adapters."""

    @property
    @abstractmethod
    def name(self) -> Label:
        """Stable provider name recorded in each result and page provenance."""

    @property
    @abstractmethod
    def version(self) -> Label:
        """Adapter/output version, bumped whenever produced text can change."""

    @abstractmethod
    def recognize_page(self, page: OcrPageInput) -> OcrPageResult:
        """Recognize one rendered page and return expected failures as data."""
