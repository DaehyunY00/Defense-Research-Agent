"""Tests for direct PDF page-text extraction with pypdfium2."""

from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from defense_research_agent.search.parsers import (
    DocumentParser,
    ParserCapability,
    ParserErrorCode,
    PdfiumPdfParser,
)

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "pdf_pages"


def _fixture(name: str) -> Path:
    return FIXTURE_DIR / name


def _checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_adapter_claims_pdf_and_only_declares_actual_capabilities() -> None:
    parser: DocumentParser = PdfiumPdfParser()

    assert parser.name == "pypdfium2-pdf"
    assert parser.version == "1.0.0"
    assert parser.supports(Path("document.PDF"))
    assert not parser.supports(Path("document.json"))
    assert parser.capabilities == frozenset(
        {
            ParserCapability.TEXT,
            ParserCapability.PAGE_TEXT,
            ParserCapability.OCR_SIGNAL,
        }
    )
    assert ParserCapability.TABLES not in parser.capabilities


def test_normal_multi_page_extraction_preserves_pdfium_text_and_provenance() -> None:
    source_path = _fixture("defense_policy_research.pdf")
    checksum = _checksum(source_path)
    parser = PdfiumPdfParser()

    result = parser.parse(source_path, checksum)

    assert [(page.page_number, page.text) for page in result.pages] == [
        (1, "Policy Research page 1 line 1\r\nPolicy Research page 1 line 2"),
        (2, "Policy Research page 2"),
    ]
    assert result.failures == []
    assert result.requires_ocr is False
    assert result.provenance.parser_name == parser.name
    assert result.provenance.parser_version == parser.version
    assert result.provenance.source_checksum == checksum
    assert all(page.provenance == result.provenance for page in result.pages)
    assert all(page.section_title is None for page in result.pages)


@pytest.mark.parametrize(
    ("fixture_name", "expected_pages"),
    [
        (
            "defense_forum.pdf",
            [(1, "Defense Forum page 1"), (2, "Defense Forum page 2")],
        ),
        ("kida_brief.pdf", [(1, "KIDA Brief page 1")]),
        (
            "defense_policy_research.pdf",
            [
                (1, "Policy Research page 1 line 1\r\nPolicy Research page 1 line 2"),
                (2, "Policy Research page 2"),
            ],
        ),
        (
            "research_report.pdf",
            [(1, "Research Report page 1"), (3, "Research Report page 3")],
        ),
    ],
    ids=["defense-forum", "kida-brief", "defense-policy-research", "research-report"],
)
def test_representative_publication_type_fixtures_map_original_page_numbers(
    fixture_name: str,
    expected_pages: list[tuple[int, str]],
) -> None:
    source_path = _fixture(fixture_name)

    result = PdfiumPdfParser().parse(source_path, _checksum(source_path))

    assert [(page.page_number, page.text) for page in result.pages] == expected_pages


def test_empty_page_is_reported_and_other_pages_survive() -> None:
    source_path = _fixture("research_report.pdf")

    result = PdfiumPdfParser().parse(source_path, _checksum(source_path))

    assert [page.page_number for page in result.pages] == [1, 3]
    assert [(failure.code, failure.page_number) for failure in result.failures] == [
        (ParserErrorCode.EMPTY_PAGE, 2)
    ]
    assert result.requires_ocr is False


def test_all_empty_pages_report_empty_document_and_do_not_guess_ocr() -> None:
    source_path = _fixture("empty_document.pdf")

    result = PdfiumPdfParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert [(failure.code, failure.page_number) for failure in result.failures] == [
        (ParserErrorCode.EMPTY_PAGE, 1),
        (ParserErrorCode.EMPTY_PAGE, 2),
        (ParserErrorCode.EMPTY_DOCUMENT, None),
    ]
    assert result.requires_ocr is False


def test_full_page_image_without_text_is_evidence_for_ocr() -> None:
    source_path = _fixture("scanned_page.pdf")

    result = PdfiumPdfParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert [failure.code for failure in result.failures] == [
        ParserErrorCode.EMPTY_PAGE,
        ParserErrorCode.EMPTY_DOCUMENT,
    ]
    assert result.requires_ocr is True


@pytest.mark.parametrize(
    "fixture_name",
    ["invalid_header.pdf", "corrupt.pdf"],
    ids=["header-mismatch", "corrupt-structure"],
)
def test_invalid_pdf_is_reported_instead_of_raised(fixture_name: str) -> None:
    source_path = _fixture(fixture_name)

    result = PdfiumPdfParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.CORRUPT_STRUCTURE


def test_encrypted_pdf_is_reported_instead_of_raised() -> None:
    source_path = _fixture("encrypted.pdf")

    result = PdfiumPdfParser().parse(source_path, _checksum(source_path))

    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.ENCRYPTED


def test_checksum_mismatch_is_detected_before_pdf_loading() -> None:
    source_path = _fixture("corrupt.pdf")
    actual_checksum = _checksum(source_path)

    result = PdfiumPdfParser().parse(source_path, "f" * 64)

    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is ParserErrorCode.CHECKSUM_MISMATCH
    assert result.provenance.source_checksum == actual_checksum


def test_page_decode_failure_is_scoped_and_other_page_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _fixture("defense_forum.pdf")
    parser = PdfiumPdfParser()
    original_extract = parser._extract_page_text
    call_count = 0

    def fail_first_page(text_page: Any) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise UnicodeDecodeError("utf-16-le", b"\x00\xd8", 0, 2, "unpaired surrogate")
        return original_extract(text_page)

    monkeypatch.setattr(parser, "_extract_page_text", fail_first_page)

    result = parser.parse(source_path, _checksum(source_path))

    assert [(page.page_number, page.text) for page in result.pages] == [(2, "Defense Forum page 2")]
    assert [(failure.code, failure.page_number) for failure in result.failures] == [
        (ParserErrorCode.DECODE_ERROR, 1)
    ]


def test_valid_unassigned_unicode_codepoint_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _fixture("kida_brief.pdf")
    parser = PdfiumPdfParser()
    extracted_text = "unassigned:\u0378"
    monkeypatch.setattr(parser, "_extract_page_text", lambda _: extracted_text)

    result = parser.parse(source_path, _checksum(source_path))

    assert result.failures == []
    assert result.pages[0].text == extracted_text
    assert result.pages[0].text.encode("utf-8") == extracted_text.encode("utf-8")


def test_same_input_produces_byte_identical_result() -> None:
    source_path = _fixture("defense_forum.pdf")
    parser = PdfiumPdfParser()
    checksum = _checksum(source_path)

    first = parser.parse(source_path, checksum).model_dump_json().encode("utf-8")
    second = parser.parse(source_path, checksum).model_dump_json().encode("utf-8")

    assert first == second


def test_extraction_does_not_change_source_file_hash() -> None:
    source_path = _fixture("defense_forum.pdf")
    before = _checksum(source_path)

    PdfiumPdfParser().parse(source_path, before)

    assert _checksum(source_path) == before


def test_pdfium_document_page_and_text_resources_are_explicitly_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _fixture("defense_forum.pdf")
    closed: Counter[str] = Counter()
    parser = PdfiumPdfParser()
    original_document_close = parser._close_document
    original_page_close = parser._close_page
    original_text_close = parser._close_text_page

    def close_document(document: Any) -> None:
        closed["document"] += 1
        original_document_close(document)

    def close_page(page: Any) -> None:
        closed["page"] += 1
        original_page_close(page)

    def close_text(text_page: Any) -> None:
        closed["text"] += 1
        original_text_close(text_page)

    monkeypatch.setattr(parser, "_close_document", close_document)
    monkeypatch.setattr(parser, "_close_page", close_page)
    monkeypatch.setattr(parser, "_close_text_page", close_text)

    result = parser.parse(source_path, _checksum(source_path))

    assert len(result.pages) == 2
    assert closed == Counter({"page": 2, "text": 2, "document": 1})


def test_unsupported_type_and_unreadable_source_are_returned_as_failures(
    tmp_path: Path,
) -> None:
    parser = PdfiumPdfParser()

    unsupported = parser.parse(tmp_path / "document.json", "a" * 64)
    unreadable = parser.parse(tmp_path / "missing.pdf", "a" * 64)

    assert unsupported.failures[0].code is ParserErrorCode.UNSUPPORTED_FORMAT
    assert unreadable.failures[0].code is ParserErrorCode.UNREADABLE_SOURCE
