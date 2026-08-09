"""Document parser contract tests, exercised through a fake adapter."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import ExtractionProvenance, PublicationPage
from defense_research_agent.search import (
    DocumentParser,
    ParserCapability,
    ParserErrorCode,
    ParseResult,
    ParserFailure,
)

SOURCE_CHECKSUM = "b" * 64


class FakePdfParser(DocumentParser):
    """Minimal in-memory adapter proving the interface is implementable."""

    def __init__(self, pages: dict[int, str], *, encrypted: bool = False) -> None:
        self._pages = pages
        self._encrypted = encrypted

    @property
    def name(self) -> str:
        return "fake-pdf"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def capabilities(self) -> frozenset[ParserCapability]:
        return frozenset({ParserCapability.TEXT, ParserCapability.PAGE_TEXT})

    def supports(self, source_path: Path) -> bool:
        return source_path.suffix.casefold() == ".pdf"

    def parse(self, source_path: Path, source_checksum: str) -> ParseResult:
        provenance = ExtractionProvenance(
            parser_name=self.name,
            parser_version=self.version,
            source_checksum=source_checksum,
        )
        if self._encrypted:
            return ParseResult(
                provenance=provenance,
                failures=[
                    ParserFailure(
                        code=ParserErrorCode.ENCRYPTED,
                        message="암호화된 문서",
                    )
                ],
            )

        pages: list[PublicationPage] = []
        failures: list[ParserFailure] = []
        for number in sorted(self._pages):
            text = self._pages[number]
            if not text.strip():
                failures.append(
                    ParserFailure(
                        code=ParserErrorCode.EMPTY_PAGE,
                        message="본문 없음",
                        page_number=number,
                    )
                )
                continue
            pages.append(PublicationPage(page_number=number, text=text, provenance=provenance))
        return ParseResult(
            provenance=provenance,
            pages=pages,
            failures=failures,
            requires_ocr=not pages,
        )


def _provenance() -> ExtractionProvenance:
    return ExtractionProvenance(
        parser_name="fake-pdf",
        parser_version="0.1.0",
        source_checksum=SOURCE_CHECKSUM,
    )


def test_successful_parse_records_pages_and_provenance() -> None:
    parser = FakePdfParser({1: "첫 페이지", 2: "둘째 페이지"})

    result = parser.parse(Path("report.pdf"), SOURCE_CHECKSUM)

    assert [page.page_number for page in result.pages] == [1, 2]
    assert all(page.provenance == result.provenance for page in result.pages)
    assert result.provenance.parser_name == "fake-pdf"
    assert result.provenance.source_checksum == SOURCE_CHECKSUM
    assert result.failures == []
    assert not result.is_empty


def test_partial_extraction_keeps_good_pages_and_reports_the_bad_one() -> None:
    parser = FakePdfParser({1: "첫 페이지", 2: "   ", 3: "셋째 페이지"})

    result = parser.parse(Path("report.pdf"), SOURCE_CHECKSUM)

    assert [page.page_number for page in result.pages] == [1, 3]
    assert [failure.page_number for failure in result.failures] == [2]
    assert result.failures[0].code is ParserErrorCode.EMPTY_PAGE


def test_expected_failure_is_returned_not_raised() -> None:
    parser = FakePdfParser({}, encrypted=True)

    result = parser.parse(Path("report.pdf"), SOURCE_CHECKSUM)

    assert result.is_empty
    assert result.failures[0].code is ParserErrorCode.ENCRYPTED


def test_parser_declares_which_sources_it_claims() -> None:
    parser = FakePdfParser({1: "본문"})

    assert parser.supports(Path("a.PDF"))
    assert not parser.supports(Path("a.json"))


def test_empty_parse_without_a_failure_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one failure"):
        ParseResult(provenance=_provenance())


def test_pages_must_be_ordered_by_page_number() -> None:
    with pytest.raises(ValidationError, match="ascending page_number"):
        ParseResult(
            provenance=_provenance(),
            pages=[
                PublicationPage(page_number=2, text="둘", provenance=_provenance()),
                PublicationPage(page_number=1, text="하나", provenance=_provenance()),
            ],
        )


def test_page_numbers_must_not_repeat() -> None:
    with pytest.raises(ValidationError, match="must not repeat"):
        ParseResult(
            provenance=_provenance(),
            pages=[
                PublicationPage(page_number=1, text="하나", provenance=_provenance()),
                PublicationPage(page_number=1, text="중복", provenance=_provenance()),
            ],
        )


def test_provenance_rejects_a_non_sha256_source_checksum() -> None:
    with pytest.raises(ValidationError):
        ExtractionProvenance(
            parser_name="fake-pdf",
            parser_version="0.1.0",
            source_checksum="not-a-checksum",
        )


def test_provenance_round_trips_through_json() -> None:
    provenance = _provenance()

    restored = ExtractionProvenance.model_validate_json(provenance.model_dump_json())

    assert restored == provenance


def test_provenance_carries_no_timestamp_field() -> None:
    assert "extracted_at" not in ExtractionProvenance.model_fields


def test_publication_page_requires_extraction_provenance() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        PublicationPage.model_validate({"page_number": 1, "text": "본문"})


def test_publication_page_rejects_provenance_without_parser_version() -> None:
    with pytest.raises(ValidationError, match="parser_version"):
        PublicationPage.model_validate(
            {
                "page_number": 1,
                "text": "본문",
                "provenance": {
                    "parser_name": "fake-pdf",
                    "source_checksum": SOURCE_CHECKSUM,
                },
            }
        )
