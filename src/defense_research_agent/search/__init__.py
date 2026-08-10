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
from defense_research_agent.search.metadata import (
    METADATA_NORMALIZATION_VERSION,
    RULE_BASED_METADATA_EXTRACTOR_VERSION,
    PublicationMetadataExtractor,
    RuleBasedPublicationMetadataExtractor,
    normalize_metadata_text,
)
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
    "METADATA_NORMALIZATION_VERSION",
    "RULE_BASED_METADATA_EXTRACTOR_VERSION",
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
    "RuleBasedPublicationMetadataExtractor",
    "SearchMatch",
    "normalize_metadata_text",
    "select_page_text_result",
]
