"""Tests for the observed metadata JSON page adapter."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from defense_research_agent.search.parsers import (
    DocumentParser,
    JsonPageParser,
    ParserCapability,
    ParserErrorCode,
)

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "json_pages" / "observed_document.json"


def _checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _fixture_payload() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload


def _write_payload(tmp_path: Path, payload: object) -> Path:
    source_path = tmp_path / "document.json"
    source_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return source_path


def test_adapter_claims_json_and_declares_its_capabilities() -> None:
    parser: DocumentParser = JsonPageParser()

    assert parser.name == "json-page-texts"
    assert parser.version == "1.0.0"
    assert parser.supports(Path("document.JSON"))
    assert not parser.supports(Path("document.pdf"))
    assert parser.capabilities == frozenset(
        {
            ParserCapability.TEXT,
            ParserCapability.PAGE_TEXT,
            ParserCapability.OCR_SIGNAL,
        }
    )


def test_unsupported_file_type_is_reported_instead_of_read(tmp_path: Path) -> None:
    source_path = tmp_path / "document.pdf"

    result = JsonPageParser().parse(source_path, "a" * 64)

    assert result.pages == []
    assert result.failures[0].code is ParserErrorCode.UNSUPPORTED_FORMAT


def test_unsorted_pages_are_sorted_with_exact_provenance_and_no_section() -> None:
    parser = JsonPageParser()
    checksum = _checksum(FIXTURE_PATH)

    result = parser.parse(FIXTURE_PATH, checksum)

    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert [page.text for page in result.pages] == [
        "첫 번째 페이지",
        "두 번째 페이지",
        "세 번째 페이지",
    ]
    assert all(page.section_title is None for page in result.pages)
    assert all(page.provenance == result.provenance for page in result.pages)
    assert result.provenance.parser_name == parser.name
    assert result.provenance.parser_version == parser.version
    assert result.provenance.source_checksum == checksum
    assert result.failures == []
    assert result.requires_ocr is False


def test_same_input_produces_byte_identical_result() -> None:
    parser = JsonPageParser()
    checksum = _checksum(FIXTURE_PATH)

    first = parser.parse(FIXTURE_PATH, checksum).model_dump_json().encode("utf-8")
    second = parser.parse(FIXTURE_PATH, checksum).model_dump_json().encode("utf-8")

    assert first == second


def test_declared_char_count_is_advisory_and_actual_text_is_preserved(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["page_texts"][0]["char_count"] = 999
    source_path = _write_payload(tmp_path, payload)

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert result.failures == []
    assert result.pages[1].page_number == 2
    assert result.pages[1].text == "두 번째 페이지"


def test_duplicate_page_number_is_reported_and_ambiguous_pages_are_excluded(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload()
    payload["page_texts"] = [
        {"page": 1, "text": "첫 후보", "char_count": 4},
        {"page": 1, "text": "둘째 후보", "char_count": 5},
        {"page": 3, "text": "살아남는 페이지", "char_count": 8},
    ]
    source_path = _write_payload(tmp_path, payload)

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert [page.page_number for page in result.pages] == [3]
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.CORRUPT_STRUCTURE
    assert result.failures[0].page_number == 1
    assert result.failures[0].message == "duplicate page number"
    assert result.requires_ocr is False


def test_blank_page_reports_empty_page_keeps_other_pages_and_requires_ocr(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload()
    payload["page_texts"][0]["text"] = " \t\n"
    payload["page_texts"][0]["char_count"] = 3
    source_path = _write_payload(tmp_path, payload)

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert [page.page_number for page in result.pages] == [1, 3]
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.EMPTY_PAGE
    assert result.failures[0].page_number == 2
    assert result.requires_ocr is True


@pytest.mark.parametrize("remove_field", [False, True], ids=["empty", "missing"])
def test_missing_or_empty_page_texts_reports_empty_document(
    tmp_path: Path,
    *,
    remove_field: bool,
) -> None:
    payload = _fixture_payload()
    if remove_field:
        del payload["page_texts"]
    else:
        payload["page_texts"] = []
    source_path = _write_payload(tmp_path, payload)

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.EMPTY_DOCUMENT
    assert result.requires_ocr is True


@pytest.mark.parametrize(
    "bad_page",
    [
        {"text": "page 필드 없음", "char_count": 10},
        {"page": "1", "text": "문자열 번호", "char_count": 6},
        {"page": True, "text": "불리언 번호", "char_count": 6},
        {"page": 0, "text": "0번 페이지", "char_count": 6},
    ],
    ids=["missing", "string", "boolean", "zero"],
)
def test_invalid_page_number_reports_failure_and_preserves_valid_page(
    tmp_path: Path,
    bad_page: dict[str, object],
) -> None:
    payload = _fixture_payload()
    payload["page_texts"] = [
        bad_page,
        {"page": 2, "text": "정상 페이지", "char_count": 6},
    ]
    source_path = _write_payload(tmp_path, payload)

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert [page.page_number for page in result.pages] == [2]
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.CORRUPT_STRUCTURE
    assert result.failures[0].page_number is None
    assert result.requires_ocr is False


def test_non_string_page_text_reports_failure(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["page_texts"] = [{"page": 1, "text": 123, "char_count": 3}]
    source_path = _write_payload(tmp_path, payload)

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.CORRUPT_STRUCTURE
    assert result.failures[0].page_number == 1
    assert result.requires_ocr is False


def test_checksum_mismatch_is_reported_before_content_is_parsed() -> None:
    actual_checksum = _checksum(FIXTURE_PATH)

    result = JsonPageParser().parse(FIXTURE_PATH, "f" * 64)

    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.CHECKSUM_MISMATCH
    assert result.provenance.source_checksum == actual_checksum
    assert result.requires_ocr is False


def test_unreadable_source_is_reported_instead_of_raised(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.json"

    result = JsonPageParser().parse(missing_path, "a" * 64)

    assert result.pages == []
    assert result.failures[0].code is ParserErrorCode.UNREADABLE_SOURCE
    assert result.provenance.source_checksum == "a" * 64


def test_json_parse_failure_is_reported_instead_of_raised(tmp_path: Path) -> None:
    source_path = tmp_path / "broken.json"
    source_path.write_text('{"page_texts": [', encoding="utf-8")

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert result.failures[0].code is ParserErrorCode.CORRUPT_STRUCTURE


def test_invalid_utf8_is_reported_instead_of_raised(tmp_path: Path) -> None:
    source_path = tmp_path / "invalid-utf8.json"
    source_path.write_bytes(b"\xff\xfe")

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert result.failures[0].code is ParserErrorCode.DECODE_ERROR


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"page_texts": None},
        {"page_texts": ["not-an-object"]},
    ],
    ids=["non-object-root", "non-list-pages", "non-object-page"],
)
def test_malformed_page_structure_is_reported(tmp_path: Path, payload: object) -> None:
    source_path = _write_payload(tmp_path, payload)

    result = JsonPageParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert result.failures[0].code is ParserErrorCode.CORRUPT_STRUCTURE
    assert result.requires_ocr is False
