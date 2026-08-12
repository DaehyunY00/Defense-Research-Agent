"""Deterministic publication-level lexical/vector rank fusion."""

from defense_research_agent.search.hybrid.algorithm import (
    ChunkVectorSearch,
    HybridSearchAlgorithm,
    HybridSearchContractError,
    LexicalPublicationSearch,
)
from defense_research_agent.search.hybrid.models import (
    DEFAULT_CANDIDATE_LIMIT_PER_SOURCE,
    DEFAULT_RRF_K,
    HYBRID_FILTER_STAGE,
    HYBRID_TIE_BREAKER,
    RRF_FUSION_STRATEGY,
    RRF_FUSION_VERSION,
    HybridFailureCode,
    HybridFusionTrace,
    HybridSearchFailure,
    HybridSearchMatch,
    HybridSearchResult,
    HybridSearchStatus,
    HybridVectorIndexTrace,
    HybridVectorStatus,
)

__all__ = [
    "DEFAULT_CANDIDATE_LIMIT_PER_SOURCE",
    "DEFAULT_RRF_K",
    "HYBRID_FILTER_STAGE",
    "HYBRID_TIE_BREAKER",
    "RRF_FUSION_STRATEGY",
    "RRF_FUSION_VERSION",
    "ChunkVectorSearch",
    "HybridFailureCode",
    "HybridFusionTrace",
    "HybridSearchAlgorithm",
    "HybridSearchContractError",
    "HybridSearchFailure",
    "HybridSearchMatch",
    "HybridSearchResult",
    "HybridSearchStatus",
    "HybridVectorIndexTrace",
    "HybridVectorStatus",
    "LexicalPublicationSearch",
]
