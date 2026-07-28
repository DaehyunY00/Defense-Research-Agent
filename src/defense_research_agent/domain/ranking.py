"""Configurable deterministic ranking and diversity domain models."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, NonNegativeFloat, PositiveInt, model_validator

from defense_research_agent.domain.common import (
    Confidence,
    DomainModel,
    EntityId,
    Score,
)
from defense_research_agent.domain.evaluation import EvaluationCriterion
from defense_research_agent.domain.topic import RecommendedOutputType, TopicCandidate

type Weight = Annotated[float, Field(ge=0.0, le=1.0)]


class RankingWeights(DomainModel):
    """Seven criterion weights whose sum must be exactly one within tolerance."""

    policy_relevance: Weight
    timeliness: Weight
    novelty: Weight
    public_evidence_sufficiency: Weight
    policy_impact: Weight
    feasibility: Weight
    output_fit: Weight

    @model_validator(mode="after")
    def validate_total(self) -> "RankingWeights":
        if abs(sum(self.as_mapping().values()) - 1.0) > 1e-9:
            raise ValueError("ranking weights must sum to 1.0")
        return self

    def as_mapping(self) -> dict[EvaluationCriterion, float]:
        """Return criterion-keyed weights for pure score calculation."""
        return {
            EvaluationCriterion.POLICY_RELEVANCE: self.policy_relevance,
            EvaluationCriterion.TIMELINESS: self.timeliness,
            EvaluationCriterion.NOVELTY: self.novelty,
            EvaluationCriterion.PUBLIC_EVIDENCE_SUFFICIENCY: (self.public_evidence_sufficiency),
            EvaluationCriterion.POLICY_IMPACT: self.policy_impact,
            EvaluationCriterion.FEASIBILITY: self.feasibility,
            EvaluationCriterion.OUTPUT_FIT: self.output_fit,
        }


class PenaltySettings(DomainModel):
    """Configurable point deductions applied before diversity selection."""

    direct_duplicate: NonNegativeFloat
    official_material_missing: NonNegativeFloat
    scope_too_broad: NonNegativeFloat
    foreign_case_only: NonNegativeFloat
    insufficient_evidence_ids: NonNegativeFloat
    low_confidence: NonNegativeFloat
    missing_criterion: NonNegativeFloat


class RankingThresholds(DomainModel):
    """Deterministic thresholds shared across evaluation and ranking."""

    minimum_evidence_ids: PositiveInt
    low_confidence: Confidence
    direct_title_similarity: Confidence
    high_score_without_evidence: Score


class DiversitySettings(DomainModel):
    """Repeat penalties for optional greedy diversity selection."""

    enabled: bool = True
    domain_repeat_penalty: NonNegativeFloat
    country_repeat_penalty: NonNegativeFloat
    output_repeat_penalty: NonNegativeFloat
    horizon_repeat_penalty: NonNegativeFloat


class RankingConfig(DomainModel):
    """Validated ranking configuration loaded from a local JSON file."""

    weights: RankingWeights
    penalties: PenaltySettings
    thresholds: RankingThresholds
    diversity: DiversitySettings


class ResearchHorizon(StrEnum):
    """Coarse time horizon used only for portfolio diversity."""

    SHORT_TERM = "short_term"
    STRUCTURAL = "structural"
    UNKNOWN = "unknown"


class CandidateAttributes(DomainModel):
    """Explainable attributes used by diversity adjustment."""

    policy_domains: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    output_type: RecommendedOutputType | None = None
    research_horizon: ResearchHorizon = ResearchHorizon.UNKNOWN


class ScoreAdjustment(DomainModel):
    """One named point deduction and its evidence-based explanation."""

    code: str
    amount: NonNegativeFloat
    reason: str


class RankedTopic(DomainModel):
    """Candidate with raw, penalized, diversified, and explainable scores."""

    candidate: TopicCandidate
    rank: PositiveInt
    criterion_scores: dict[str, Score] = Field(default_factory=dict)
    raw_score: Score
    penalties: list[ScoreAdjustment] = Field(default_factory=list)
    penalized_score: Score
    diversity_adjustments: list[ScoreAdjustment] = Field(default_factory=list)
    adjusted_score: Score
    confidence: Confidence | None = None
    evidence_ids: list[EntityId] = Field(default_factory=list)
    attributes: CandidateAttributes
    explanation: list[str] = Field(default_factory=list)
