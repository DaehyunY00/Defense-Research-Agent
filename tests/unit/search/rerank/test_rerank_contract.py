"""Validation and failure-policy tests for the reranker contract."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

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


def _ranked(
    candidate_id: str,
    *,
    original_rank: int,
    rank: int,
    score: float | None = None,
) -> RerankedCandidate:
    return RerankedCandidate(
        candidate_id=candidate_id,
        original_rank=original_rank,
        rank=rank,
        rerank_score=score,
    )


def _result(**overrides: object) -> RerankResult:
    values: dict[str, object] = {
        "provider_name": "contract-test",
        "provider_version": "1.0.0",
        "model_id": "contract-test-model",
        "status": RerankStatus.SUCCEEDED,
        "input_candidate_ids": ["candidate-a", "candidate-b"],
        "ranked_candidates": [
            _ranked("candidate-b", original_rank=2, rank=1, score=0.9),
            _ranked("candidate-a", original_rank=1, rank=2, score=0.8),
        ],
    }
    values.update(overrides)
    return RerankResult.model_validate(values)


class _ExplodingReranker(Reranker):
    @property
    def provider_name(self) -> str:
        return "exploding-test"

    @property
    def provider_version(self) -> str:
        return "1.0.0"

    @property
    def model_id(self) -> str:
        return "exploding-test-model"

    @property
    def max_candidates(self) -> int:
        return 10

    def _rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> RerankExecution:
        del query, candidates
        raise RuntimeError("provider response with secret details")


class _ForeignInputReranker(Reranker):
    @property
    def provider_name(self) -> str:
        return "foreign-input-test"

    @property
    def provider_version(self) -> str:
        return "1.0.0"

    @property
    def model_id(self) -> str:
        return "foreign-input-test-model"

    @property
    def max_candidates(self) -> int:
        return 10

    def _rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> RerankExecution:
        del query, candidates
        return RerankExecution(
            result=RerankResult(
                provider_name=self.provider_name,
                provider_version=self.provider_version,
                model_id=self.model_id,
                status=RerankStatus.SUCCEEDED,
                input_candidate_ids=["candidate-a", "invented"],
                ranked_candidates=[
                    _ranked("invented", original_rank=2, rank=1, score=1.0),
                ],
            ),
            trace=RerankTrace(
                latency_ms=4.5,
                cost_usd=Decimal("0.01"),
                input_units=2,
                output_units=1,
            ),
        )


def test_result_rejects_more_candidates_than_declared_input() -> None:
    with pytest.raises(ValidationError, match="cannot return more candidates"):
        _result(
            input_candidate_ids=["candidate-a"],
            ranked_candidates=[
                _ranked("candidate-a", original_rank=1, rank=1),
                _ranked("candidate-a", original_rank=1, rank=2),
            ],
        )


def test_result_rejects_candidate_absent_from_declared_input() -> None:
    with pytest.raises(ValidationError, match="absent from its input"):
        _result(
            ranked_candidates=[
                _ranked("invented", original_rank=1, rank=1),
            ]
        )


def test_result_rejects_duplicate_input_and_output_ids() -> None:
    with pytest.raises(ValidationError, match="input candidate IDs must be unique"):
        _result(input_candidate_ids=["candidate-a", "candidate-a"])

    with pytest.raises(ValidationError, match="one candidate more than once"):
        _result(
            ranked_candidates=[
                _ranked("candidate-a", original_rank=1, rank=1),
                _ranked("candidate-a", original_rank=1, rank=2),
            ]
        )


def test_result_rejects_incorrect_original_or_returned_rank() -> None:
    with pytest.raises(ValidationError, match="original_rank"):
        _result(
            ranked_candidates=[
                _ranked("candidate-b", original_rank=1, rank=1),
            ]
        )

    with pytest.raises(ValidationError, match="contiguous"):
        _result(
            ranked_candidates=[
                _ranked("candidate-b", original_rank=2, rank=2),
            ]
        )


def test_result_allows_a_valid_truncated_success() -> None:
    result = _result(
        ranked_candidates=[
            _ranked("candidate-b", original_rank=2, rank=1, score=0.75),
        ]
    )

    assert [item.candidate_id for item in result.ranked_candidates] == ["candidate-b"]
    assert result.ranked_candidates[0].rerank_score == 0.75


def test_failure_result_cannot_hide_failure_or_change_upstream_order() -> None:
    failure = RerankFailure(
        code=RerankErrorCode.PROVIDER_ERROR,
        message="visible failure",
    )
    with pytest.raises(ValidationError, match="must include a failure"):
        _result(status=RerankStatus.FAILED, failure=None)

    with pytest.raises(ValidationError, match="preserve original candidate order"):
        _result(
            status=RerankStatus.FAILED,
            failure=failure,
            ranked_candidates=[
                _ranked("candidate-b", original_rank=2, rank=1),
                _ranked("candidate-a", original_rank=1, rank=2),
            ],
        )


def test_non_finite_scores_and_invalid_trace_measurements_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RerankCandidate(candidate_id="candidate-a", text="text", source_score=float("inf"))
    with pytest.raises(ValidationError):
        _ranked("candidate-a", original_rank=1, rank=1, score=float("nan"))
    with pytest.raises(ValidationError):
        RerankTrace(latency_ms=-0.1, cost_usd=Decimal("0"))
    with pytest.raises(ValidationError):
        RerankTrace(latency_ms=0.0, cost_usd=Decimal("-0.01"))


def test_provider_exception_returns_visible_failure_without_leaking_exception_text() -> None:
    execution = _ExplodingReranker().rerank(
        "query",
        [
            RerankCandidate(candidate_id="candidate-a", text="first"),
            RerankCandidate(candidate_id="candidate-b", text="second"),
        ],
    )

    assert execution.result.status is RerankStatus.FAILED
    assert execution.result.failure is not None
    assert execution.result.failure.code is RerankErrorCode.PROVIDER_ERROR
    assert "secret details" not in execution.result.failure.message
    assert [item.candidate_id for item in execution.result.ranked_candidates] == [
        "candidate-a",
        "candidate-b",
    ]
    assert execution.trace.latency_ms is None
    assert execution.trace.cost_usd is None


def test_public_boundary_rejects_response_for_foreign_input_snapshot() -> None:
    execution = _ForeignInputReranker().rerank(
        "query",
        [
            RerankCandidate(candidate_id="candidate-a", text="first"),
            RerankCandidate(candidate_id="candidate-b", text="second"),
        ],
    )

    assert execution.result.status is RerankStatus.FAILED
    assert execution.result.failure is not None
    assert execution.result.failure.code is RerankErrorCode.INVALID_RESPONSE
    assert [item.candidate_id for item in execution.result.ranked_candidates] == [
        "candidate-a",
        "candidate-b",
    ]
    assert execution.trace.latency_ms == 4.5
    assert execution.trace.cost_usd == Decimal("0.01")
