"""Document parser interfaces and format adapters."""

from defense_research_agent.search.parsers.base import (
    DocumentParser,
    ParserCapability,
    ParserErrorCode,
    ParseResult,
    ParserFailure,
)
from defense_research_agent.search.parsers.json_page_parser import JsonPageParser

__all__ = [
    "DocumentParser",
    "JsonPageParser",
    "ParseResult",
    "ParserCapability",
    "ParserErrorCode",
    "ParserFailure",
]
