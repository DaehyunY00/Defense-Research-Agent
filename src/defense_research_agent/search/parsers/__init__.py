"""Document parser interfaces and format adapters."""

from defense_research_agent.search.parsers.base import (
    DocumentParser,
    ParserCapability,
    ParserErrorCode,
    ParseResult,
    ParserFailure,
)
from defense_research_agent.search.parsers.json_page_parser import JsonPageParser
from defense_research_agent.search.parsers.page_text_selection import (
    PageTextSelection,
    PageTextSource,
    select_page_text_result,
)
from defense_research_agent.search.parsers.pdfium_pdf_parser import PdfiumPdfParser

__all__ = [
    "DocumentParser",
    "JsonPageParser",
    "PageTextSelection",
    "PageTextSource",
    "ParseResult",
    "ParserCapability",
    "ParserErrorCode",
    "ParserFailure",
    "PdfiumPdfParser",
    "select_page_text_result",
]
