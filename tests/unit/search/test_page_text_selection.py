"""Tests for deterministic JSON/PDF page-text source selection."""

import pytest

from defense_research_agent.domain import ExtractionProvenance, PublicationPage
from defense_research_agent.search.parsers import (
    PageTextSource,
    ParserErrorCode,
    ParseResult,
    ParserFailure,
    select_page_text_result,
)


def _result(parser_name: str, pages: list[tuple[int, str]]) -> ParseResult:
    provenance = ExtractionProvenance(
        parser_name=parser_name,
        parser_version="1.0.0",
        source_checksum=("a" if parser_name == "pdf" else "b") * 64,
    )
    if pages:
        return ParseResult(
            provenance=provenance,
            pages=[
                PublicationPage(page_number=number, text=text, provenance=provenance)
                for number, text in pages
            ],
        )
    return ParseResult(
        provenance=provenance,
        failures=[
            ParserFailure(
                code=ParserErrorCode.EMPTY_DOCUMENT,
                message="no usable page text",
            )
        ],
    )


def test_pdf_is_default_and_disagreement_is_exposed_without_merging() -> None:
    pdf_result = _result("pdf", [(1, "PDF page 1")])
    json_result = _result("json", [(1, "JSON page 1"), (2, "JSON page 2")])

    selection = select_page_text_result(pdf_result=pdf_result, json_result=json_result)

    assert selection.selected_source is PageTextSource.PDF
    assert selection.result is pdf_result
    assert selection.sources_match is False
    assert selection.used_fallback is False
    assert [page.page_number for page in selection.result.pages] == [1]
    assert selection.json_result is json_result


def test_exact_page_number_and_text_sequences_are_reported_as_matching() -> None:
    pdf_result = _result("pdf", [(1, "same"), (2, "same too")])
    json_result = _result("json", [(1, "same"), (2, "same too")])

    selection = select_page_text_result(pdf_result=pdf_result, json_result=json_result)

    assert selection.selected_source is PageTextSource.PDF
    assert selection.sources_match is True


def test_json_is_fallback_only_when_pdf_has_no_usable_pages() -> None:
    pdf_result = _result("pdf", [])
    json_result = _result("json", [(1, "observed JSON text")])

    selection = select_page_text_result(pdf_result=pdf_result, json_result=json_result)

    assert selection.selected_source is PageTextSource.JSON_PAGE_TEXTS
    assert selection.result is json_result
    assert selection.used_fallback is True
    assert selection.sources_match is None
    assert selection.pdf_result is pdf_result


def test_both_empty_results_keep_primary_pdf_failure_visible() -> None:
    pdf_result = _result("pdf", [])
    json_result = _result("json", [])

    selection = select_page_text_result(pdf_result=pdf_result, json_result=json_result)

    assert selection.selected_source is PageTextSource.PDF
    assert selection.result is pdf_result
    assert selection.used_fallback is False


def test_json_can_be_selected_when_pdf_extraction_was_not_run() -> None:
    json_result = _result("json", [(1, "observed JSON text")])

    selection = select_page_text_result(pdf_result=None, json_result=json_result)

    assert selection.selected_source is PageTextSource.JSON_PAGE_TEXTS
    assert selection.result is json_result
    assert selection.used_fallback is False


def test_selection_requires_at_least_one_parser_result() -> None:
    with pytest.raises(ValueError, match="at least one parser result"):
        select_page_text_result(pdf_result=None, json_result=None)
