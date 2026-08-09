"""Embedding provider interfaces and adapters."""

from defense_research_agent.search.embeddings.base import (
    EmbeddingBatchResult,
    EmbeddingErrorCode,
    EmbeddingFailure,
    EmbeddingProvider,
    EmbeddingVector,
)

__all__ = [
    "EmbeddingBatchResult",
    "EmbeddingErrorCode",
    "EmbeddingFailure",
    "EmbeddingProvider",
    "EmbeddingVector",
]
