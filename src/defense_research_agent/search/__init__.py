"""Search, chunking, and indexing interfaces."""

from defense_research_agent.search.base import PublicationSearchAlgorithm, SearchMatch
from defense_research_agent.search.chunking import (
    DEFAULT_CHUNKING_VERSION,
    DeterministicPageChunker,
    PublicationChunker,
)
from defense_research_agent.search.lexical import LocalLexicalSearchAlgorithm

__all__ = [
    "DEFAULT_CHUNKING_VERSION",
    "DeterministicPageChunker",
    "LocalLexicalSearchAlgorithm",
    "PublicationChunker",
    "PublicationSearchAlgorithm",
    "SearchMatch",
]
