"""Embedding provider interfaces and adapters."""

from defense_research_agent.search.embeddings.base import (
    EmbeddingBatchResult,
    EmbeddingErrorCode,
    EmbeddingFailure,
    EmbeddingProvider,
    EmbeddingVector,
)
from defense_research_agent.search.embeddings.fake import FakeEmbeddingProvider

__all__ = [
    "EmbeddingBatchResult",
    "EmbeddingErrorCode",
    "EmbeddingFailure",
    "EmbeddingProvider",
    "EmbeddingVector",
    "FakeEmbeddingProvider",
]
