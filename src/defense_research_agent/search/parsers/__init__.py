"""Document parser interfaces and format adapters."""

from defense_research_agent.search.parsers.base import (
    DocumentParser,
    ParserCapability,
    ParserErrorCode,
    ParseResult,
    ParserFailure,
)

__all__ = [
    "DocumentParser",
    "ParseResult",
    "ParserCapability",
    "ParserErrorCode",
    "ParserFailure",
]
