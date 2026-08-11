"""Tests for deterministic checksum-keyed fake OCR behavior."""

from hashlib import sha256

from defense_research_agent.search.ocr import (
    FakeOcrFixture,
    FakeOcrProvider,
    OcrErrorCode,
    OcrPageInput,
    OcrProvider,
)


def _page(page_number: int, image_bytes: bytes) -> OcrPageInput:
    return OcrPageInput(
        page_number=page_number,
        image_bytes=image_bytes,
        input_checksum=sha256(image_bytes).hexdigest(),
    )


def test_fake_implements_provider_contract_and_replays_exact_text() -> None:
    page = _page(4, b"fixture-image")
    provider = FakeOcrProvider({page.input_checksum: FakeOcrFixture.success(" 원문\r\n", 0.91)})

    result = provider.recognize_page(page)

    assert isinstance(provider, OcrProvider)
    assert result.text == " 원문\r\n"
    assert result.confidence == 0.91
    assert result.provider_name == provider.name
    assert result.provider_version == provider.version
    assert result.input_checksum == page.input_checksum


def test_same_input_and_configuration_are_byte_identical() -> None:
    success_page = _page(1, b"success")
    timeout_page = _page(2, b"timeout")
    fixtures = {
        success_page.input_checksum: FakeOcrFixture.success("결정적 OCR", 0.88),
        timeout_page.input_checksum: FakeOcrFixture.timeout(),
    }
    first_provider = FakeOcrProvider(fixtures)
    second_provider = FakeOcrProvider(fixtures)

    first = [
        first_provider.recognize_page(page).model_dump_json().encode("utf-8")
        for page in (success_page, timeout_page)
    ]
    second = [
        second_provider.recognize_page(page).model_dump_json().encode("utf-8")
        for page in (success_page, timeout_page)
    ]

    assert first == second


def test_fake_copies_fixtures_at_construction() -> None:
    page = _page(1, b"copied")
    fixture = FakeOcrFixture.success("처음", 0.9)
    provider = FakeOcrProvider({page.input_checksum: fixture})

    fixture.text = "나중 변경"

    assert provider.recognize_page(page).text == "처음"


def test_fake_returns_timeout_and_provider_error_as_data() -> None:
    timeout_page = _page(1, b"timeout")
    unknown_page = _page(2, b"not-configured")
    provider = FakeOcrProvider({timeout_page.input_checksum: FakeOcrFixture.timeout()})

    timeout_result = provider.recognize_page(timeout_page)
    missing_result = provider.recognize_page(unknown_page)

    assert timeout_result.failure is not None
    assert timeout_result.failure.code is OcrErrorCode.TIMEOUT
    assert missing_result.failure is not None
    assert missing_result.failure.code is OcrErrorCode.PROVIDER_ERROR
