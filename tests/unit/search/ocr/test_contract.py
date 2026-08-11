"""Contract tests for page-level OCR inputs and results."""

from hashlib import sha256

import pytest
from pydantic import ValidationError

from defense_research_agent.search.ocr import (
    OcrErrorCode,
    OcrPageFailure,
    OcrPageInput,
    OcrPageResult,
)

CHECKSUM = sha256(b"rendered-page").hexdigest()


def test_page_input_requires_checksum_of_exact_rendered_bytes() -> None:
    page = OcrPageInput(
        page_number=3,
        image_bytes=b"rendered-page",
        input_checksum=CHECKSUM,
    )

    assert page.page_number == 3
    assert page.input_checksum == CHECKSUM


def test_page_input_rejects_mismatched_checksum() -> None:
    with pytest.raises(ValidationError, match="does not match image_bytes"):
        OcrPageInput(
            page_number=3,
            image_bytes=b"different-page",
            input_checksum=CHECKSUM,
        )


def test_success_preserves_raw_text_confidence_and_provider_lineage() -> None:
    raw_text = "  OCR 원문\r\n조합 e\u0301  "

    result = OcrPageResult(
        page_number=3,
        input_checksum=CHECKSUM,
        provider_name="fixture-ocr",
        provider_version="2.1.0",
        text=raw_text,
        confidence=0.87,
    )

    assert result.is_success
    assert result.text == raw_text
    assert result.confidence == 0.87
    assert result.input_checksum == CHECKSUM


def test_timeout_is_a_page_result_instead_of_an_exception() -> None:
    result = OcrPageResult(
        page_number=3,
        input_checksum=CHECKSUM,
        provider_name="fixture-ocr",
        provider_version="2.1.0",
        failure=OcrPageFailure(
            code=OcrErrorCode.TIMEOUT,
            message="page processing timed out",
        ),
    )

    assert not result.is_success
    assert result.failure is not None
    assert result.failure.code is OcrErrorCode.TIMEOUT
    assert result.text is None
    assert result.confidence is None


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "본문"},
        {"confidence": 0.9},
        {
            "text": "본문",
            "confidence": 0.9,
            "failure": {"code": "provider_error", "message": "failure"},
        },
    ],
)
def test_result_rejects_incomplete_or_mixed_success_failure_shape(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        OcrPageResult.model_validate(
            {
                "page_number": 3,
                "input_checksum": CHECKSUM,
                "provider_name": "fixture-ocr",
                "provider_version": "2.1.0",
                **payload,
            }
        )
