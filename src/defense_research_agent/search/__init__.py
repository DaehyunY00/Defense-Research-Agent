"""Search, chunking, and indexing interfaces."""

from defense_research_agent.search.base import PublicationSearchAlgorithm, SearchMatch
from defense_research_agent.search.chunking import (
    DEFAULT_CHUNKING_VERSION,
    DeterministicPageChunker,
    PublicationChunker,
)
from defense_research_agent.search.embeddings import (
    EmbeddingBatchResult,
    EmbeddingErrorCode,
    EmbeddingFailure,
    EmbeddingProvider,
    EmbeddingVector,
    FakeEmbeddingProvider,
)
from defense_research_agent.search.lexical import LocalLexicalSearchAlgorithm
from defense_research_agent.search.metadata import PublicationMetadataExtractor
from defense_research_agent.search.parsers import (
    DocumentParser,
    JsonPageParser,
    PageTextSelection,
    PageTextSource,
    ParserCapability,
    ParserErrorCode,
    ParseResult,
    ParserFailure,
    PdfiumPdfParser,
    select_page_text_result,
)

__all__ = [
    "DEFAULT_CHUNKING_VERSION",
    "DeterministicPageChunker",
    "DocumentParser",
    "EmbeddingBatchResult",
    "EmbeddingErrorCode",
    "EmbeddingFailure",
    "EmbeddingProvider",
    "EmbeddingVector",
    "FakeEmbeddingProvider",
    "JsonPageParser",
    "LocalLexicalSearchAlgorithm",
    "PageTextSelection",
    "PageTextSource",
    "ParseResult",
    "ParserCapability",
    "ParserErrorCode",
    "ParserFailure",
    "PdfiumPdfParser",
    "PublicationChunker",
    "PublicationMetadataExtractor",
    "PublicationSearchAlgorithm",
    "SearchMatch",
    "select_page_text_result",
]
