"""Provider-neutral reranking contract and deterministic offline fake."""

from defense_research_agent.search.rerank.base import (
    RerankCandidate,
    RerankedCandidate,
    Reranker,
    RerankErrorCode,
    RerankExecution,
    RerankFailure,
    RerankResult,
    RerankStatus,
    RerankTrace,
)
from defense_research_agent.search.rerank.fake import DEFAULT_MAX_CANDIDATES, FakeReranker

__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "FakeReranker",
    "RerankCandidate",
    "RerankErrorCode",
    "RerankExecution",
    "RerankFailure",
    "RerankResult",
    "RerankStatus",
    "RerankTrace",
    "RerankedCandidate",
    "Reranker",
]
