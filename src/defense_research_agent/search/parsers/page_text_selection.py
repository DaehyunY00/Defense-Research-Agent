"""Deterministic selection between direct PDF and observed JSON page text."""

from enum import StrEnum

from pydantic import model_validator

from defense_research_agent.domain.common import DomainModel
from defense_research_agent.search.parsers.base import ParseResult


class PageTextSource(StrEnum):
    """Available page-text sources in deterministic preference order."""

    PDF = "pdf"
    JSON_PAGE_TEXTS = "json_page_texts"


class PageTextSelection(DomainModel):
    """Auditable source-selection decision retaining both parser outcomes."""

    selected_source: PageTextSource
    pdf_result: ParseResult | None = None
    json_result: ParseResult | None = None
    sources_match: bool | None = None
    used_fallback: bool = False

    @property
    def result(self) -> ParseResult:
        """Return the selected parser result."""
        if self.selected_source is PageTextSource.PDF:
            assert self.pdf_result is not None
            return self.pdf_result
        assert self.json_result is not None
        return self.json_result

    @model_validator(mode="after")
    def selected_result_must_exist(self) -> "PageTextSelection":
        """Reject a decision naming a source whose result is absent."""
        if self.selected_source is PageTextSource.PDF and self.pdf_result is None:
            raise ValueError("selected PDF result is missing")
        if self.selected_source is PageTextSource.JSON_PAGE_TEXTS and self.json_result is None:
            raise ValueError("selected JSON result is missing")
        return self


def select_page_text_result(
    *,
    pdf_result: ParseResult | None,
    json_result: ParseResult | None,
) -> PageTextSelection:
    """Select one whole-document result with direct PDF as the default.

    A PDF result with at least one usable page always wins. JSON is a fallback
    only when a PDF result exists but has no usable pages, or when PDF extraction
    was not run. Pages are never merged across sources because that would obscure
    partial failures and mix parser provenance within one extraction decision.

    When both sources have usable pages, ``sources_match`` compares the exact
    ordered ``(page_number, text)`` sequence. A disagreement is exposed as
    ``False`` but does not change the PDF-first choice. If neither has usable
    pages, the PDF result remains selected so its primary extraction failure is
    not hidden by an equally empty fallback.
    """
    if pdf_result is None and json_result is None:
        raise ValueError("at least one parser result is required")

    sources_match = _sources_match(pdf_result, json_result)
    if pdf_result is not None and pdf_result.pages:
        return PageTextSelection(
            selected_source=PageTextSource.PDF,
            pdf_result=pdf_result,
            json_result=json_result,
            sources_match=sources_match,
        )

    if json_result is not None and json_result.pages:
        return PageTextSelection(
            selected_source=PageTextSource.JSON_PAGE_TEXTS,
            pdf_result=pdf_result,
            json_result=json_result,
            sources_match=sources_match,
            used_fallback=pdf_result is not None,
        )

    if pdf_result is not None:
        return PageTextSelection(
            selected_source=PageTextSource.PDF,
            pdf_result=pdf_result,
            json_result=json_result,
            sources_match=sources_match,
        )

    return PageTextSelection(
        selected_source=PageTextSource.JSON_PAGE_TEXTS,
        json_result=json_result,
        sources_match=sources_match,
    )


def _sources_match(
    pdf_result: ParseResult | None,
    json_result: ParseResult | None,
) -> bool | None:
    if pdf_result is None or json_result is None or not pdf_result.pages or not json_result.pages:
        return None
    pdf_pages = [(page.page_number, page.text) for page in pdf_result.pages]
    json_pages = [(page.page_number, page.text) for page in json_result.pages]
    return pdf_pages == json_pages
