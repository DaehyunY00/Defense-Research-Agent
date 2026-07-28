"""Candidate evaluation inputs, results, failures, and deterministic aggregates."""

from enum import StrEnum

from pydantic import Field, NonNegativeInt

from defense_research_agent.domain.common import (
    Confidence,
    DomainModel,
    EntityId,
    Score,
)
from defense_research_agent.domain.publication import ResearchPublication
from defense_research_agent.domain.topic import TopicCandidate, TopicSignal


class EvaluationCriterion(StrEnum):
    """Canonical criteria used by evaluation agents and the Python ranker."""

    POLICY_RELEVANCE = "policy_relevance"
    TIMELINESS = "timeliness"
    NOVELTY = "novelty"
    PUBLIC_EVIDENCE_SUFFICIENCY = "public_evidence_sufficiency"
    POLICY_IMPACT = "policy_impact"
    FEASIBILITY = "feasibility"
    OUTPUT_FIT = "output_fit"

    @classmethod
    def _missing_(cls, value: object) -> "EvaluationCriterion | None":
        if not isinstance(value, str):
            return None
        aliases = {
            "정책 관련성": cls.POLICY_RELEVANCE,
            "국방정책 관련성": cls.POLICY_RELEVANCE,
            "시의성": cls.TIMELINESS,
            "신규성": cls.NOVELTY,
            "공개 근거 충분성": cls.PUBLIC_EVIDENCE_SUFFICIENCY,
            "정책적 영향": cls.POLICY_IMPACT,
            "연구 수행 가능성": cls.FEASIBILITY,
            "산출물 적합성": cls.OUTPUT_FIT,
        }
        return aliases.get(value.strip().casefold())


class EvaluatorName(StrEnum):
    """Stable evaluator identities for audit records."""

    POLICY_RELEVANCE = "policy_relevance_evaluator"
    NOVELTY = "novelty_evaluator"
    EVIDENCE_FEASIBILITY = "evidence_feasibility_evaluator"
    OUTPUT_FIT = "output_fit_evaluator"


ALL_EVALUATION_CRITERIA = tuple(EvaluationCriterion)


class EvaluationResult(DomainModel):
    """A single criterion score with rationale, evidence, and uncertainty."""

    candidate_id: EntityId
    criterion: EvaluationCriterion
    score: Score
    rationale: str
    evidence_ids: list[EntityId] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: Confidence


class EvaluationResultBatch(DomainModel):
    """Structured model response for one independent evaluator."""

    results: list[EvaluationResult] = Field(default_factory=list)


class CandidateEvaluationInput(DomainModel):
    """Evidence snapshot supplied independently to every evaluator."""

    candidate: TopicCandidate
    signals: list[TopicSignal] = Field(default_factory=list)
    related_publications: list[ResearchPublication] = Field(default_factory=list)
    similar_publications: list[ResearchPublication] = Field(default_factory=list)


class EvaluationFailure(DomainModel):
    """A bounded evaluator failure that does not invalidate the candidate."""

    candidate_id: EntityId
    evaluator: EvaluatorName
    attempts: NonNegativeInt
    error_type: str
    message: str


class CandidateEvaluation(DomainModel):
    """All independent results and failures for one candidate."""

    candidate_id: EntityId
    results: list[EvaluationResult] = Field(default_factory=list)
    failures: list[EvaluationFailure] = Field(default_factory=list)
    missing_criteria: list[EvaluationCriterion] = Field(default_factory=list)
    attempt_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)


class AggregatedCandidateEvaluation(DomainModel):
    """Python-computed candidate summary before configurable ranking."""

    candidate_id: EntityId
    criterion_scores: dict[str, Score] = Field(default_factory=dict)
    composite_score: Score | None = None
    confidence: Confidence | None = None
    evidence_ids: list[EntityId] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    failures: list[EvaluationFailure] = Field(default_factory=list)
    missing_criteria: list[EvaluationCriterion] = Field(default_factory=list)
