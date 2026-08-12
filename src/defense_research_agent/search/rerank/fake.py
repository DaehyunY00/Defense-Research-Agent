"""Deterministic identity reranker for contract and pipeline tests."""

from decimal import Decimal

from defense_research_agent.search.rerank.base import (
    RerankCandidate,
    RerankedCandidate,
    Reranker,
    RerankErrorCode,
    RerankExecution,
    RerankResult,
    RerankStatus,
    RerankTrace,
)

DEFAULT_MAX_CANDIDATES = 100


class FakeReranker(Reranker):
    """Preserve input order deterministically without external dependencies.

    For identical candidate IDs and configuration, this fake guarantees a
    byte-identical serialized :class:`RerankResult`. It uses no model, network,
    credentials, clock, randomness, locale, or filesystem. Query and candidate
    text are deliberately not inspected, so instructions or prompt-injection
    attempts embedded in untrusted candidate text cannot affect its behavior.

    It guarantees only contract compliance, stable identity ordering, explicit
    simulated failure, and zero provider latency/cost trace. It does not score
    relevance, improve ranking or retrieval quality, model a real provider's
    behavior, or justify any quality claim. Those questions require the P2.6
    golden dataset and benchmark.
    """

    def __init__(
        self,
        *,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        fail: bool = False,
    ) -> None:
        """Configure the input bound and optional deterministic failure mode."""
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise TypeError("max_candidates must be an int")
        if max_candidates <= 0:
            raise ValueError("max_candidates must be greater than zero")
        if not isinstance(fail, bool):
            raise TypeError("fail must be a bool")
        self._max_candidates = max_candidates
        self._fail = fail

    @property
    def provider_name(self) -> str:
        """Return the stable provider name for the offline fake."""
        return "fake-identity"

    @property
    def provider_version(self) -> str:
        """Return the identity-ranking algorithm version."""
        return "1.0.0"

    @property
    def model_id(self) -> str:
        """Return the explicit non-model identifier recorded in results."""
        return "fake-identity-reranker"

    @property
    def max_candidates(self) -> int:
        """Return the configured provider invocation limit."""
        return self._max_candidates

    def _rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> RerankExecution:
        del query  # Query and candidate text are intentionally not interpreted.
        candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
        trace = RerankTrace(
            latency_ms=0.0,
            cost_usd=Decimal("0"),
            input_units=0,
            output_units=0,
        )
        if self._fail:
            return self._failure_execution(
                candidate_ids,
                code=RerankErrorCode.PROVIDER_ERROR,
                message="configured fake reranker failure; original order preserved",
                trace=trace,
            )

        ranked_candidates = [
            RerankedCandidate(
                candidate_id=candidate_id,
                original_rank=rank,
                rank=rank,
                rerank_score=None,
            )
            for rank, candidate_id in enumerate(candidate_ids, start=1)
        ]
        return RerankExecution(
            result=RerankResult(
                provider_name=self.provider_name,
                provider_version=self.provider_version,
                model_id=self.model_id,
                status=RerankStatus.SUCCEEDED,
                input_candidate_ids=list(candidate_ids),
                ranked_candidates=ranked_candidates,
            ),
            trace=trace,
        )
