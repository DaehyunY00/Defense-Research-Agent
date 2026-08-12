"""Deterministic chunk-level vector search contracts and local implementation."""

from defense_research_agent.search.vector.algorithm import (
    PublicationChunkFactory,
    PublicationVectorSearchAdapter,
    VectorQueryEmbeddingError,
    VectorSearchAlgorithm,
    VectorSearchConfigurationError,
    VectorSearchError,
)
from defense_research_agent.search.vector.index import (
    InMemoryVectorIndex,
    VectorIndex,
    VectorIndexBuildError,
    VectorIndexError,
    VectorIndexNotBuiltError,
    canonical_vector_manifest_bytes,
    write_vector_index_artifacts,
)
from defense_research_agent.search.vector.models import (
    VECTOR_ENTRIES_FILENAME,
    VECTOR_INDEX_MANIFEST_VERSION,
    VECTOR_MANIFEST_FILENAME,
    VECTOR_SIMILARITY_METRIC,
    VECTOR_TIE_BREAKER,
    VectorIndexManifest,
    VectorNormalization,
    VectorSearchMatch,
)

__all__ = [
    "VECTOR_ENTRIES_FILENAME",
    "VECTOR_INDEX_MANIFEST_VERSION",
    "VECTOR_MANIFEST_FILENAME",
    "VECTOR_SIMILARITY_METRIC",
    "VECTOR_TIE_BREAKER",
    "InMemoryVectorIndex",
    "PublicationChunkFactory",
    "PublicationVectorSearchAdapter",
    "VectorIndex",
    "VectorIndexBuildError",
    "VectorIndexError",
    "VectorIndexManifest",
    "VectorIndexNotBuiltError",
    "VectorNormalization",
    "VectorQueryEmbeddingError",
    "VectorSearchAlgorithm",
    "VectorSearchConfigurationError",
    "VectorSearchError",
    "VectorSearchMatch",
    "canonical_vector_manifest_bytes",
    "write_vector_index_artifacts",
]
