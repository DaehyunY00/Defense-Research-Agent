"""Reproducible pilot evaluation metric and temporal backtest models."""

from enum import StrEnum

from pydantic import Field, NonNegativeInt

from defense_research_agent.domain.common import DomainModel, EntityId


class MetricStatus(StrEnum):
    """Whether a metric was calculated from evidence or lacks a required gold set."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class NodeExecutionStatus(StrEnum):
    """Auditable outcome for a workflow node in one pilot run."""

    SUCCESS = "success"
    PARTIAL = "partial"
    AWAITING_REVIEW = "awaiting_review"
    FAILURE = "failure"
    NOT_RUN = "not_run"


class OrchestrationAudit(DomainModel):
    """Execution facts used by the harness instead of inferred node metrics."""

    node_statuses: dict[str, NodeExecutionStatus] = Field(default_factory=dict)
    retry_count: NonNegativeInt
    resumed_after_interrupt: bool | None = None
    reproduction_match: bool | None = None


class MetricResult(DomainModel):
    """One transparent metric with numerator, denominator, and limitations."""

    status: MetricStatus
    value: float | int | None = None
    numerator: float | int | None = None
    denominator: float | int | None = None
    reason: str


class TemporalBacktestSummary(DomainModel):
    """Leakage-safe filename-year split without fabricated topic matching."""

    cutoff_year: int
    input_publication_count: NonNegativeInt
    future_target_count: NonNegativeInt
    unknown_year_count: NonNegativeInt
    leakage_count: NonNegativeInt
    future_topic_comparison: MetricResult


class PilotEvaluationSummary(DomainModel):
    """All pilot metrics and visible failure cases."""

    run_id: EntityId
    metrics: dict[str, dict[str, MetricResult]] = Field(default_factory=dict)
    temporal_backtest: TemporalBacktestSummary
    top_candidate_ids: list[EntityId] = Field(default_factory=list)
    failure_cases: list[str] = Field(default_factory=list)
