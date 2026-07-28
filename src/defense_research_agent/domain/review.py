"""Human-review decisions, append-only events, and topic planning cards."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field, PositiveInt, model_validator

from defense_research_agent.domain.common import DomainModel, EntityId, Label, Score
from defense_research_agent.domain.topic import RecommendedOutputType


class ReviewDecisionType(StrEnum):
    """Allowed human decisions for a ranked research topic."""

    APPROVE = "approve"
    APPROVE_WITH_EDITS = "approve_with_edits"
    HOLD = "hold"
    REJECT = "reject"


class ReviewEdits(DomainModel):
    """Researcher-authored candidate fields used only after explicit approval."""

    working_title: Label | None = None
    research_question: str | None = None
    trigger: str | None = None
    internal_context: str | None = None
    novelty_claim: str | None = None
    recommended_output: RecommendedOutputType | None = None
    known_limitations: list[str] | None = None

    @model_validator(mode="after")
    def require_at_least_one_edit(self) -> "ReviewEdits":
        if all(
            value is None
            for value in (
                self.working_title,
                self.research_question,
                self.trigger,
                self.internal_context,
                self.novelty_claim,
                self.recommended_output,
                self.known_limitations,
            )
        ):
            raise ValueError("review edits must change at least one candidate field")
        return self


class ReviewSubmission(DomainModel):
    """One researcher decision submitted through the CLI or graph resume input."""

    candidate_id: EntityId
    decision: ReviewDecisionType
    reviewer: Label
    edits: ReviewEdits | None = None
    comment: str | None = None

    @model_validator(mode="after")
    def validate_edits_for_decision(self) -> "ReviewSubmission":
        if self.decision is ReviewDecisionType.APPROVE_WITH_EDITS and self.edits is None:
            raise ValueError("approve_with_edits requires edits")
        if self.decision is not ReviewDecisionType.APPROVE_WITH_EDITS and self.edits is not None:
            raise ValueError("edits are allowed only with approve_with_edits")
        return self


class ReviewEvent(DomainModel):
    """Immutable append-only record of one human decision."""

    event_id: EntityId
    run_id: EntityId
    candidate_id: EntityId
    decision: ReviewDecisionType
    reviewer: Label
    edits: ReviewEdits | None = None
    comment: str | None = None
    reviewed_at: datetime
    sequence: PositiveInt

    @model_validator(mode="after")
    def validate_reviewed_at(self) -> "ReviewEvent":
        if self.reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return self


class ReviewWorkflowStatus(StrEnum):
    """Review gate state used by the interrupt-equivalent graph node."""

    AWAITING_REVIEW = "awaiting_review"
    READY_FOR_CARDS = "ready_for_cards"
    COMPLETED_WITHOUT_APPROVAL = "completed_without_approval"


class ReviewGateResult(DomainModel):
    """Current latest-decision view derived from append-only history."""

    run_id: EntityId
    status: ReviewWorkflowStatus
    latest_events: list[ReviewEvent] = Field(default_factory=list)
    approved_candidate_ids: list[EntityId] = Field(default_factory=list)
    pending_candidate_ids: list[EntityId] = Field(default_factory=list)


class EvidenceReference(DomainModel):
    """Compact reference shown to a human reviewer."""

    evidence_id: EntityId
    title: str | None = None
    evidence_type: str


class TopicPlanningCard(DomainModel):
    """Final planning card produced only for a human-approved candidate."""

    candidate_id: EntityId
    working_title: Label
    research_question: str
    trigger: str | None = None
    why_now: str | None = None
    related_defense_forum: list[EvidenceReference] = Field(default_factory=list)
    related_kida_brief: list[EvidenceReference] = Field(default_factory=list)
    related_defense_policy_research: list[EvidenceReference] = Field(default_factory=list)
    novelty: str | None = None
    internal_evidence: list[EvidenceReference] = Field(default_factory=list)
    external_evidence: list[EvidenceReference] = Field(default_factory=list)
    evaluation_scores: dict[str, Score] = Field(default_factory=dict)
    evidence_sufficiency: str
    recommended_output: RecommendedOutputType | None = None
    expected_outline: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
    further_research: list[str] = Field(default_factory=list)
