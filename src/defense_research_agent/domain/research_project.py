"""Persistent project, asynchronous execution, and human-review contracts."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Checksum,
    DomainModel,
    EntityId,
    Label,
)
from defense_research_agent.domain.research_lab import (
    DataSensitivity,
    RequiredText,
    ResearchBrief,
)


class ResearchProjectStatus(StrEnum):
    """Deterministic lifecycle for one asynchronously executed research project."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    HELD = "held"
    FAILED = "failed"


class ResearchLabReviewDecision(StrEnum):
    """Human-only decisions over a completed research-lab report."""

    APPROVE = "approve"
    APPROVE_WITH_EDITS = "approve_with_edits"
    HOLD = "hold"
    REJECT = "reject"


class CreateResearchProject(DomainModel):
    """User-authored public-data research request before server ID assignment."""

    question: RequiredText
    objective: RequiredText
    scope: list[Label] = Field(default_factory=list, max_length=20)
    constraints: list[Label] = Field(default_factory=list, max_length=20)
    deliverables: list[Label] = Field(
        default_factory=lambda: ["검토 가능한 연구보고서"],
        min_length=1,
        max_length=20,
    )
    evidence_start_date: date | None = None
    evidence_end_date: date | None = None
    data_sensitivity: Literal[DataSensitivity.PUBLIC_ONLY] = DataSensitivity.PUBLIC_ONLY

    @model_validator(mode="after")
    def validate_evidence_date_range(self) -> "CreateResearchProject":
        if (
            self.evidence_start_date is not None
            and self.evidence_end_date is not None
            and self.evidence_start_date > self.evidence_end_date
        ):
            raise ValueError("evidence_start_date must be on or before evidence_end_date")
        return self

    def to_brief(self, project_id: str) -> ResearchBrief:
        """Bind a server-generated project ID to the validated request."""
        return ResearchBrief(
            project_id=project_id,
            question=self.question,
            objective=self.objective,
            scope=self.scope,
            constraints=self.constraints,
            deliverables=self.deliverables,
            evidence_start_date=self.evidence_start_date,
            evidence_end_date=self.evidence_end_date,
            data_sensitivity=self.data_sensitivity,
        )


class ResearchLabReviewSubmission(DomainModel):
    """One explicit human decision; edits are notes, never automatic mutations."""

    decision: ResearchLabReviewDecision
    reviewer: Label
    comment: str | None = Field(default=None, max_length=4_000)
    requested_edits: list[Label] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_requested_edits(self) -> "ResearchLabReviewSubmission":
        if (
            self.decision is ResearchLabReviewDecision.APPROVE_WITH_EDITS
            and not self.requested_edits
        ):
            raise ValueError("approve_with_edits requires at least one requested edit")
        if (
            self.decision
            in {
                ResearchLabReviewDecision.APPROVE,
                ResearchLabReviewDecision.HOLD,
                ResearchLabReviewDecision.REJECT,
            }
            and self.requested_edits
        ):
            raise ValueError("requested edits are allowed only with approve_with_edits")
        return self


class ResearchLabReviewEvent(DomainModel):
    """Immutable human-review event embedded in the project audit record."""

    event_id: EntityId
    decision: ResearchLabReviewDecision
    reviewer: Label
    comment: str | None = Field(default=None, max_length=4_000)
    requested_edits: list[Label] = Field(default_factory=list, max_length=30)
    reviewed_at: datetime
    sequence: PositiveInt

    @model_validator(mode="after")
    def validate_review_event(self) -> "ResearchLabReviewEvent":
        if self.reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        if (
            self.decision is ResearchLabReviewDecision.APPROVE_WITH_EDITS
            and not self.requested_edits
        ):
            raise ValueError("approve_with_edits review event requires requested edits")
        if (
            self.decision is not ResearchLabReviewDecision.APPROVE_WITH_EDITS
            and self.requested_edits
        ):
            raise ValueError(
                "review event requested edits are allowed only with approve_with_edits"
            )
        return self


class ResearchProjectRecord(DomainModel):
    """Compact Firestore record; the full research run remains in object storage."""

    project_id: EntityId
    brief: ResearchBrief
    status: ResearchProjectStatus
    created_at: datetime
    updated_at: datetime
    execution_name: str | None = Field(default=None, max_length=1_000)
    result_object: str | None = Field(default=None, max_length=1_000)
    result_sha256: Checksum | None = None
    result_size_bytes: NonNegativeInt | None = None
    failure_code: Label | None = None
    failure_message: str | None = Field(default=None, max_length=500)
    review_history: list[ResearchLabReviewEvent] = Field(default_factory=list, max_length=100)
    attempt_count: NonNegativeInt = 0
    revision: NonNegativeInt = 0
    human_approval_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_state(self) -> "ResearchProjectRecord":
        if self.brief.project_id != self.project_id:
            raise ValueError("project record and brief project_id must match")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("project timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        completed_statuses = {
            ResearchProjectStatus.AWAITING_HUMAN_REVIEW,
            ResearchProjectStatus.APPROVED,
            ResearchProjectStatus.REJECTED,
            ResearchProjectStatus.HELD,
        }
        if self.status in completed_statuses and (
            self.result_object is None
            or self.result_sha256 is None
            or self.result_size_bytes is None
        ):
            raise ValueError("completed research project requires a bound result artifact")
        if self.status is ResearchProjectStatus.FAILED and self.failure_code is None:
            raise ValueError("failed research project requires failure_code")
        if self.status is not ResearchProjectStatus.FAILED and (
            self.failure_code is not None or self.failure_message is not None
        ):
            raise ValueError("only failed projects may contain failure details")
        if (
            self.status
            in {
                ResearchProjectStatus.APPROVED,
                ResearchProjectStatus.REJECTED,
                ResearchProjectStatus.HELD,
            }
            and not self.review_history
        ):
            raise ValueError("reviewed status requires a human-review event")
        if self.review_history:
            reviewed_statuses = {
                ResearchProjectStatus.APPROVED,
                ResearchProjectStatus.REJECTED,
                ResearchProjectStatus.HELD,
            }
            if self.status not in reviewed_statuses:
                raise ValueError("only reviewed projects may contain review history")
            expected_sequences = list(range(1, len(self.review_history) + 1))
            actual_sequences = [event.sequence for event in self.review_history]
            if actual_sequences != expected_sequences:
                raise ValueError("review history sequence must be contiguous from 1")
            event_ids = [event.event_id for event in self.review_history]
            if len(set(event_ids)) != len(event_ids):
                raise ValueError("review history event IDs must be unique")
            review_times = [event.reviewed_at for event in self.review_history]
            if review_times != sorted(review_times):
                raise ValueError("review history timestamps must be chronological")
            if any(
                reviewed_at < self.created_at or reviewed_at > self.updated_at
                for reviewed_at in review_times
            ):
                raise ValueError("review history timestamps must be within the project lifetime")
            expected_status = _status_for_review_decision(self.review_history[-1].decision)
            if self.status is not expected_status:
                raise ValueError("project status must match the last human-review decision")
        return self


def utc_now() -> datetime:
    """Return one timezone-aware timestamp for default runtime injection."""
    return datetime.now(UTC)


def _status_for_review_decision(
    decision: ResearchLabReviewDecision,
) -> ResearchProjectStatus:
    if decision in {
        ResearchLabReviewDecision.APPROVE,
        ResearchLabReviewDecision.APPROVE_WITH_EDITS,
    }:
        return ResearchProjectStatus.APPROVED
    if decision is ResearchLabReviewDecision.REJECT:
        return ResearchProjectStatus.REJECTED
    return ResearchProjectStatus.HELD
