"""Deterministic offline OCR provider for contract and fallback tests."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

from pydantic import model_validator

from defense_research_agent.domain.common import Checksum, Confidence, DomainModel
from defense_research_agent.search.ocr.base import (
    OcrErrorCode,
    OcrPageFailure,
    OcrPageInput,
    OcrPageResult,
    OcrProvider,
)


class FakeOcrFixture(DomainModel):
    """Configured success or expected failure for one exact page checksum."""

    text: str | None = None
    confidence: Confidence | None = None
    failure: OcrPageFailure | None = None

    @classmethod
    def success(cls, text: str, confidence: float) -> Self:
        """Build a successful fixture while preserving ``text`` exactly."""
        return cls(text=text, confidence=confidence)

    @classmethod
    def timeout(cls, message: str = "fake OCR page timed out") -> Self:
        """Build a deterministic page timeout fixture."""
        return cls(failure=OcrPageFailure(code=OcrErrorCode.TIMEOUT, message=message))

    @classmethod
    def provider_error(cls, message: str = "fake OCR provider error") -> Self:
        """Build a deterministic provider-error fixture."""
        return cls(failure=OcrPageFailure(code=OcrErrorCode.PROVIDER_ERROR, message=message))

    @model_validator(mode="after")
    def fixture_shape_must_be_success_or_failure(self) -> "FakeOcrFixture":
        """Match the mutually exclusive shapes of :class:`OcrPageResult`."""
        if self.failure is None:
            if self.text is None or self.confidence is None:
                raise ValueError("successful fake OCR fixture requires text and confidence")
        elif self.text is not None or self.confidence is not None:
            raise ValueError("failed fake OCR fixture must not include text or confidence")
        return self


class FakeOcrProvider(OcrProvider):
    """Replay checksum-keyed OCR fixtures without external dependencies.

    Given identical page bytes and an identical fixture mapping, this fake
    guarantees byte-identical serialized :class:`OcrPageResult` values. It
    copies fixtures at construction, uses no clock, randomness, process,
    network, model, credentials, locale, or filesystem, and can deterministically
    represent timeout and provider-error results.

    It does not inspect pixels, perform OCR, model latency or cancellation,
    validate a rendering pipeline, reproduce a real provider's confidence, or
    make any claim about OCR accuracy, layout recovery, or language support.
    Unconfigured checksums produce a deterministic ``PROVIDER_ERROR`` result.
    """

    def __init__(self, fixtures: Mapping[Checksum, FakeOcrFixture]) -> None:
        copied = {
            checksum: fixture.model_copy(deep=True)
            for checksum, fixture in sorted(fixtures.items())
        }
        self._fixtures: Mapping[Checksum, FakeOcrFixture] = MappingProxyType(copied)

    @property
    def name(self) -> str:
        """Return the stable fake-provider name used in provenance."""
        return "fake-checksum-ocr"

    @property
    def version(self) -> str:
        """Return the fixture-replay behavior version."""
        return "1.0.0"

    def recognize_page(self, page: OcrPageInput) -> OcrPageResult:
        """Replay the fixture matching the exact rendered-page checksum."""
        fixture = self._fixtures.get(page.input_checksum)
        if fixture is None:
            return self._failure_result(
                page,
                OcrPageFailure(
                    code=OcrErrorCode.PROVIDER_ERROR,
                    message="no fake OCR fixture is configured for the page checksum",
                ),
            )
        if fixture.failure is not None:
            return self._failure_result(page, fixture.failure.model_copy(deep=True))
        if fixture.text is None or fixture.confidence is None:  # pragma: no cover
            raise AssertionError("validated successful fixture has no result")
        return OcrPageResult(
            page_number=page.page_number,
            input_checksum=page.input_checksum,
            provider_name=self.name,
            provider_version=self.version,
            text=fixture.text,
            confidence=fixture.confidence,
        )

    def _failure_result(
        self,
        page: OcrPageInput,
        failure: OcrPageFailure,
    ) -> OcrPageResult:
        return OcrPageResult(
            page_number=page.page_number,
            input_checksum=page.input_checksum,
            provider_name=self.name,
            provider_version=self.version,
            failure=failure,
        )
