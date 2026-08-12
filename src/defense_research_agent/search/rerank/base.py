"""Provider-neutral reranking contract and fail-open execution boundary.

Candidate text is untrusted retrieval data. Implementations may treat it only as
data supplied to a ranking algorithm; instructions embedded in that text never
change the contract, provider configuration, or failure policy.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import DomainModel, EntityId, Label

type RerankScore = Annotated[float, Field(allow_inf_nan=False)]
type LatencyMilliseconds = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
type CostUsd = Annotated[Decimal, Field(ge=0)]


class RerankStatus(StrEnum):
    """Whether reranking succeeded or the upstream order was preserved."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RerankErrorCode(StrEnum):
    """Stable failure taxonomy shared by reranker adapters."""

    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"


class RerankCandidate(DomainModel):
    """Provider-neutral candidate whose text must be treated as untrusted data.

    ``candidate_id`` is the only identity a result may reference. ``source_score``
    can carry a lexical, vector, or hybrid score without coupling this contract
    to one retrieval implementation. No field in a candidate is an instruction
    to the application or reranker adapter.
    """

    candidate_id: EntityId
    text: str
    source_score: RerankScore | None = None


class RerankedCandidate(DomainModel):
    """One candidate reference in returned order, with its score trace."""

    candidate_id: EntityId
    original_rank: PositiveInt
    rank: PositiveInt
    rerank_score: RerankScore | None = None


class RerankFailure(DomainModel):
    """Visible reason why the upstream candidate order was preserved."""

    code: RerankErrorCode
    message: Label


class RerankResult(DomainModel):
    """Deterministic, serializable ranking result with provider provenance.

    Operational latency and cost are intentionally absent. They live in the
    separate :class:`RerankExecution.trace` field, which does not participate in
    equality. Consequently callers can compare this model's serialized bytes for
    deterministic regression checks even when real measurements differ.

    ``input_candidate_ids`` is the validation boundary that prevents an adapter
    from returning more candidates than it received or inventing candidate IDs.
    The public :meth:`Reranker.rerank` boundary additionally checks these IDs
    against the caller's actual input snapshot.
    """

    provider_name: Label
    provider_version: Label
    model_id: Label
    status: RerankStatus
    input_candidate_ids: list[EntityId] = Field(default_factory=list)
    ranked_candidates: list[RerankedCandidate] = Field(default_factory=list)
    failure: RerankFailure | None = None

    @model_validator(mode="after")
    def candidate_contract_must_hold(self) -> "RerankResult":
        """Reject duplicate inputs, excess outputs, and invented candidates."""
        if len(self.input_candidate_ids) != len(set(self.input_candidate_ids)):
            raise ValueError("input candidate IDs must be unique")
        if len(self.ranked_candidates) > len(self.input_candidate_ids):
            raise ValueError("reranker cannot return more candidates than its input")

        input_rank_by_id = {
            candidate_id: rank
            for rank, candidate_id in enumerate(self.input_candidate_ids, start=1)
        }
        output_ids = [item.candidate_id for item in self.ranked_candidates]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("reranker cannot return one candidate more than once")

        for expected_rank, item in enumerate(self.ranked_candidates, start=1):
            original_rank = input_rank_by_id.get(item.candidate_id)
            if original_rank is None:
                raise ValueError("reranker cannot return a candidate absent from its input")
            if item.original_rank != original_rank:
                raise ValueError("original_rank must match the input candidate order")
            if item.rank != expected_rank:
                raise ValueError("rank must be contiguous and match returned list order")
        return self

    @model_validator(mode="after")
    def status_shape_and_failure_policy_must_hold(self) -> "RerankResult":
        """Make failures visible and forbid order changes on failed reranking."""
        if self.status is RerankStatus.SUCCEEDED:
            if self.failure is not None:
                raise ValueError("successful reranking must not include a failure")
            return self

        if self.failure is None:
            raise ValueError("failed reranking must include a failure")
        if len(self.ranked_candidates) != len(self.input_candidate_ids):
            raise ValueError("failed reranking must preserve every input candidate")
        for expected_rank, item in enumerate(self.ranked_candidates, start=1):
            if item.candidate_id != self.input_candidate_ids[expected_rank - 1]:
                raise ValueError("failed reranking must preserve original candidate order")
            if item.rank != expected_rank or item.original_rank != expected_rank:
                raise ValueError("failed reranking must preserve original ranks")
            if item.rerank_score is not None:
                raise ValueError("failed reranking must not invent a rerank score")
        return self


class RerankTrace(DomainModel):
    """Operational measurements kept separate from deterministic result data.

    ``None`` means that a value could not be measured, such as when an adapter
    raised before returning provider telemetry. Fake executions record exact
    zeros because they invoke no external model and incur no provider charge.
    """

    latency_ms: LatencyMilliseconds | None
    cost_usd: CostUsd | None
    input_units: NonNegativeInt | None = None
    output_units: NonNegativeInt | None = None


@dataclass(frozen=True, slots=True)
class RerankExecution:
    """A deterministic result plus non-deterministic operational trace.

    ``trace`` has ``compare=False`` by design. Equality therefore compares only
    ``result``; latency or cost changes cannot make logically identical rankings
    unequal. Serialize ``result`` for byte-level deterministic comparisons and
    persist ``trace`` alongside it as operational telemetry.
    """

    result: RerankResult
    trace: RerankTrace = field(compare=False)


class Reranker(ABC):
    """Provider-independent reranker with a visible fail-open policy.

    Reranking is an optional post-retrieval refinement. If a provider raises,
    exceeds its declared input limit, or returns a response that does not match
    the actual input, this boundary returns every candidate in its original
    upstream order with ``FAILED`` status. This preserves search availability
    without silently presenting a changed or partial ranking as trustworthy.

    Subclasses implement :meth:`_rerank`; callers always use :meth:`rerank` so
    input limits, response provenance, and fallback ordering are enforced in one
    place. Candidate text remains untrusted data throughout the call.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider name recorded on every result."""

    @property
    @abstractmethod
    def provider_version(self) -> str:
        """Adapter/output version, bumped whenever ranking behavior can change."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Provider model identifier recorded on every result."""

    @property
    @abstractmethod
    def max_candidates(self) -> int:
        """Largest candidate list accepted for one provider invocation."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> RerankExecution:
        """Rerank a snapshot of candidates or visibly preserve upstream order."""
        snapshot = tuple(candidate.model_copy(deep=True) for candidate in candidates)
        candidate_ids = tuple(candidate.candidate_id for candidate in snapshot)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("reranker input candidate IDs must be unique")

        limit = self.max_candidates
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("max_candidates must be a positive integer")
        if len(snapshot) > limit:
            return self._failure_execution(
                candidate_ids,
                code=RerankErrorCode.CANDIDATE_LIMIT_EXCEEDED,
                message=(
                    f"candidate count {len(snapshot)} exceeds max_candidates {limit}; "
                    "original order preserved"
                ),
                trace=RerankTrace(
                    latency_ms=0.0,
                    cost_usd=Decimal("0"),
                    input_units=0,
                    output_units=0,
                ),
            )

        try:
            execution = self._rerank(query, snapshot)
        except Exception:
            return self._failure_execution(
                candidate_ids,
                code=RerankErrorCode.PROVIDER_ERROR,
                message="reranker execution failed; original order preserved",
                trace=RerankTrace(
                    latency_ms=None,
                    cost_usd=None,
                    input_units=None,
                    output_units=None,
                ),
            )

        if self._response_violates_contract(execution.result, candidate_ids):
            return self._failure_execution(
                candidate_ids,
                code=RerankErrorCode.INVALID_RESPONSE,
                message="reranker returned an invalid response; original order preserved",
                trace=execution.trace,
            )
        return execution

    @abstractmethod
    def _rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> RerankExecution:
        """Perform one bounded provider call over an immutable input snapshot."""

    def _failure_execution(
        self,
        candidate_ids: tuple[EntityId, ...],
        *,
        code: RerankErrorCode,
        message: str,
        trace: RerankTrace,
    ) -> RerankExecution:
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
                status=RerankStatus.FAILED,
                input_candidate_ids=list(candidate_ids),
                ranked_candidates=ranked_candidates,
                failure=RerankFailure(code=code, message=message),
            ),
            trace=trace,
        )

    def _response_violates_contract(
        self,
        result: RerankResult,
        candidate_ids: tuple[EntityId, ...],
    ) -> bool:
        return (
            result.provider_name != self.provider_name
            or result.provider_version != self.provider_version
            or result.model_id != self.model_id
            or result.input_candidate_ids != list(candidate_ids)
            or len(result.ranked_candidates) > len(candidate_ids)
            or any(item.candidate_id not in candidate_ids for item in result.ranked_candidates)
        )
