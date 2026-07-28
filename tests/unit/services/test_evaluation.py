"""Tests for parallel evaluation, retries, evidence gates, and aggregation."""

from collections.abc import Callable
from threading import Barrier

import pytest

from defense_research_agent.agents import TopicCandidateEvaluator
from defense_research_agent.domain import (
    CandidateEvaluationInput,
    EvaluationCriterion,
    EvaluationResult,
    EvaluatorName,
    PublicationType,
    RecommendedOutputType,
    ResearchPublication,
    TopicCandidate,
    TopicSignal,
)
from defense_research_agent.repositories import InMemoryResearchPublicationRepository
from defense_research_agent.services.evaluation import (
    EvaluationRunner,
    aggregate_candidate_evaluations,
)

ResultFactory = Callable[[CandidateEvaluationInput], list[EvaluationResult]]


class _StubEvaluator(TopicCandidateEvaluator):
    def __init__(
        self,
        name: EvaluatorName,
        criteria: tuple[EvaluationCriterion, ...],
        result_factory: ResultFactory,
        *,
        barrier: Barrier | None = None,
        failures_before_success: int = 0,
    ) -> None:
        self.name = name
        self.criteria = criteria
        self._result_factory = result_factory
        self._barrier = barrier
        self._failures_before_success = failures_before_success
        self.call_count = 0
        self.received: list[CandidateEvaluationInput] = []

    def evaluate(self, evaluation_input: CandidateEvaluationInput) -> list[EvaluationResult]:
        self.call_count += 1
        self.received.append(evaluation_input)
        if self._barrier is not None:
            self._barrier.wait(timeout=2)
        if self.call_count <= self._failures_before_success:
            raise RuntimeError("simulated evaluator failure")
        return self._result_factory(evaluation_input)


def _candidate() -> TopicCandidate:
    return TopicCandidate(
        candidate_id="candidate:parallel",
        working_title="국방 AI 정책 집행 평가",
        research_question="정책 집행 성과를 어떻게 검증할 것인가?",
        recommended_output=RecommendedOutputType.KIDA_BRIEF,
        supporting_signal_ids=["signal:policy"],
        related_publication_ids=["pub:prior"],
    )


def _signal() -> TopicSignal:
    return TopicSignal(
        signal_id="signal:policy",
        signal_type="external_government_policy",
        title="국방 AI 시행계획",
        confidence=0.95,
    )


def _repository() -> InMemoryResearchPublicationRepository:
    return InMemoryResearchPublicationRepository(
        [
            ResearchPublication(
                publication_id="pub:prior",
                publication_type=PublicationType.DEFENSE_FORUM,
                title="기존 국방 AI 정책 연구",
            )
        ]
    )


def _result_factory(
    criterion: EvaluationCriterion,
    *,
    score: float = 80,
    evidence: bool = True,
) -> ResultFactory:
    def factory(evaluation_input: CandidateEvaluationInput) -> list[EvaluationResult]:
        return [
            EvaluationResult(
                candidate_id=evaluation_input.candidate.candidate_id,
                criterion=criterion,
                score=score,
                rationale="독립 평가 근거",
                evidence_ids=["pub:prior"] if evidence else [],
                confidence=0.8,
            )
        ]

    return factory


def test_all_four_evaluators_execute_concurrently_and_independently() -> None:
    barrier = Barrier(4)
    specs = [
        (EvaluatorName.POLICY_RELEVANCE, EvaluationCriterion.POLICY_RELEVANCE),
        (EvaluatorName.NOVELTY, EvaluationCriterion.NOVELTY),
        (
            EvaluatorName.EVIDENCE_FEASIBILITY,
            EvaluationCriterion.PUBLIC_EVIDENCE_SUFFICIENCY,
        ),
        (EvaluatorName.OUTPUT_FIT, EvaluationCriterion.OUTPUT_FIT),
    ]
    evaluators = [
        _StubEvaluator(
            name,
            (criterion,),
            _result_factory(criterion),
            barrier=barrier,
        )
        for name, criterion in specs
    ]
    runner = EvaluationRunner(
        evaluators,
        _repository(),
        max_retries=0,
        max_workers=4,
    )

    evaluations = runner.evaluate([_candidate()], [_signal()])

    assert len(evaluations[0].results) == 4
    assert all(evaluator.call_count == 1 for evaluator in evaluators)
    assert all(
        evaluator.received[0].candidate.candidate_id == "candidate:parallel"
        for evaluator in evaluators
    )


def test_high_score_without_evidence_is_capped_in_python() -> None:
    evaluator = _StubEvaluator(
        EvaluatorName.NOVELTY,
        (EvaluationCriterion.NOVELTY,),
        _result_factory(EvaluationCriterion.NOVELTY, score=99, evidence=False),
    )
    runner = EvaluationRunner([evaluator], _repository(), max_retries=0)

    result = runner.evaluate([_candidate()], [_signal()])[0].results[0]

    assert result.score == 60
    assert result.confidence == 0.5
    assert "high_score_without_evidence_capped" in result.risks


def test_one_evaluator_failure_is_retried_and_recorded_without_candidate_failure() -> None:
    failed = _StubEvaluator(
        EvaluatorName.NOVELTY,
        (EvaluationCriterion.NOVELTY,),
        _result_factory(EvaluationCriterion.NOVELTY),
        failures_before_success=2,
    )
    successful = _StubEvaluator(
        EvaluatorName.OUTPUT_FIT,
        (EvaluationCriterion.OUTPUT_FIT,),
        _result_factory(EvaluationCriterion.OUTPUT_FIT),
    )
    runner = EvaluationRunner(
        [failed, successful],
        _repository(),
        max_retries=1,
    )

    evaluation = runner.evaluate([_candidate()], [_signal()])[0]

    assert len(evaluation.results) == 1
    assert evaluation.results[0].criterion is EvaluationCriterion.OUTPUT_FIT
    assert evaluation.failures[0].attempts == 2
    assert evaluation.failures[0].message == "RuntimeError: evaluator execution failed"
    assert EvaluationCriterion.NOVELTY in evaluation.missing_criteria


def test_missing_result_and_aggregate_structure_are_stable() -> None:
    evaluator = _StubEvaluator(
        EvaluatorName.NOVELTY,
        (EvaluationCriterion.NOVELTY,),
        _result_factory(EvaluationCriterion.NOVELTY, score=75),
    )
    runner = EvaluationRunner([evaluator], _repository(), max_retries=0)

    first = runner.evaluate([_candidate()], [_signal()])
    second = runner.evaluate([_candidate()], [_signal()])
    first_aggregate = aggregate_candidate_evaluations(first)
    second_aggregate = aggregate_candidate_evaluations(second)

    assert first[0].missing_criteria
    assert first_aggregate[0].composite_score == 75
    assert [item.model_dump_json() for item in first_aggregate] == [
        item.model_dump_json() for item in second_aggregate
    ]


def test_runner_rejects_duplicate_candidate_and_signal_ids() -> None:
    evaluator = _StubEvaluator(
        EvaluatorName.NOVELTY,
        (EvaluationCriterion.NOVELTY,),
        _result_factory(EvaluationCriterion.NOVELTY),
    )
    runner = EvaluationRunner([evaluator], _repository(), max_retries=0)

    with pytest.raises(ValueError, match="candidate_id"):
        runner.evaluate([_candidate(), _candidate()], [_signal()])
    with pytest.raises(ValueError, match="signal_id"):
        runner.evaluate([_candidate()], [_signal(), _signal()])

    evaluation = runner.evaluate([_candidate()], [_signal()])[0]
    with pytest.raises(ValueError, match="candidate_id"):
        aggregate_candidate_evaluations([evaluation, evaluation])
