"""Parallel independent evaluation, bounded retry, and deterministic aggregation."""

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from defense_research_agent.agents.evaluators import TopicCandidateEvaluator
from defense_research_agent.domain.evaluation import (
    ALL_EVALUATION_CRITERIA,
    AggregatedCandidateEvaluation,
    CandidateEvaluation,
    CandidateEvaluationInput,
    EvaluationCriterion,
    EvaluationFailure,
    EvaluationResult,
    EvaluatorName,
)
from defense_research_agent.domain.topic import TopicCandidate, TopicSignal
from defense_research_agent.path_safety import ensure_outside_read_only_data
from defense_research_agent.repositories.base import ResearchPublicationRepository

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    evaluator: EvaluatorName
    attempts: int
    results: tuple[EvaluationResult, ...] = ()
    failure: EvaluationFailure | None = None


class EvaluationRunner:
    """Run evaluator/candidate tasks concurrently without cross-evaluator state."""

    def __init__(
        self,
        evaluators: Sequence[TopicCandidateEvaluator],
        repository: ResearchPublicationRepository,
        *,
        max_retries: int = 1,
        max_workers: int = 4,
        high_score_without_evidence: float = 60.0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if not 0 <= high_score_without_evidence <= 100:
            raise ValueError("high_score_without_evidence must be between 0 and 100")
        evaluator_names = [evaluator.name for evaluator in evaluators]
        if len(evaluator_names) != len(set(evaluator_names)):
            raise ValueError("evaluator names must be unique")
        self._evaluators = tuple(evaluators)
        self._repository = repository
        self._max_retries = max_retries
        self._max_workers = max_workers
        self._high_score_without_evidence = high_score_without_evidence

    def evaluate(
        self,
        candidates: Sequence[TopicCandidate],
        signals: Sequence[TopicSignal],
    ) -> list[CandidateEvaluation]:
        """Evaluate every candidate/evaluator pair and preserve partial failures."""
        ordered_candidates = tuple(sorted(candidates, key=lambda item: item.candidate_id))
        _require_unique_ids(
            [candidate.candidate_id for candidate in ordered_candidates],
            "candidate_id",
        )
        _require_unique_ids(
            [signal.signal_id for signal in signals],
            "signal_id",
        )
        signal_by_id = {signal.signal_id: signal for signal in signals}
        contexts = {
            candidate.candidate_id: self._build_input(candidate, signal_by_id)
            for candidate in ordered_candidates
        }
        outcomes: dict[str, list[_TaskOutcome]] = defaultdict(list)
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_candidate: dict[Future[_TaskOutcome], str] = {}
            for candidate in ordered_candidates:
                for evaluator in self._evaluators:
                    future = executor.submit(
                        self._run_with_retry,
                        evaluator,
                        contexts[candidate.candidate_id],
                    )
                    future_to_candidate[future] = candidate.candidate_id
            for future in as_completed(future_to_candidate):
                candidate_id = future_to_candidate[future]
                outcomes[candidate_id].append(future.result())

        return [
            self._candidate_evaluation(
                candidate.candidate_id,
                outcomes[candidate.candidate_id],
            )
            for candidate in ordered_candidates
        ]

    def _build_input(
        self,
        candidate: TopicCandidate,
        signal_by_id: dict[str, TopicSignal],
    ) -> CandidateEvaluationInput:
        related_publications = [
            publication
            for publication_id in candidate.related_publication_ids
            if (publication := self._repository.get_by_id(publication_id)) is not None
        ]
        similar_publications = [
            result.publication
            for result in self._repository.find_similar(
                candidate.working_title,
                candidate.research_question,
                limit=5,
            )
        ]
        return CandidateEvaluationInput(
            candidate=candidate,
            signals=[
                signal_by_id[signal_id]
                for signal_id in candidate.supporting_signal_ids
                if signal_id in signal_by_id
            ],
            related_publications=related_publications,
            similar_publications=similar_publications,
        )

    def _run_with_retry(
        self,
        evaluator: TopicCandidateEvaluator,
        evaluation_input: CandidateEvaluationInput,
    ) -> _TaskOutcome:
        attempts = 0
        while attempts <= self._max_retries:
            attempts += 1
            try:
                raw_results = evaluator.evaluate(evaluation_input)
                results = tuple(self._enforce_evidence_gate(result) for result in raw_results)
                return _TaskOutcome(
                    evaluator=evaluator.name,
                    attempts=attempts,
                    results=results,
                )
            except Exception as error:
                if attempts > self._max_retries:
                    return _TaskOutcome(
                        evaluator=evaluator.name,
                        attempts=attempts,
                        failure=EvaluationFailure(
                            candidate_id=evaluation_input.candidate.candidate_id,
                            evaluator=evaluator.name,
                            attempts=attempts,
                            error_type=type(error).__name__,
                            message=f"{type(error).__name__}: evaluator execution failed",
                        ),
                    )
        raise AssertionError("bounded retry loop must return")

    def _enforce_evidence_gate(self, result: EvaluationResult) -> EvaluationResult:
        if result.evidence_ids or result.score <= self._high_score_without_evidence:
            return result
        return EvaluationResult(
            candidate_id=result.candidate_id,
            criterion=result.criterion,
            score=self._high_score_without_evidence,
            rationale=f"{result.rationale} 근거 ID가 없어 고득점 상한을 적용했다.",
            evidence_ids=[],
            risks=list(dict.fromkeys([*result.risks, "high_score_without_evidence_capped"])),
            confidence=min(result.confidence, 0.5),
        )

    @staticmethod
    def _candidate_evaluation(
        candidate_id: str,
        outcomes: Sequence[_TaskOutcome],
    ) -> CandidateEvaluation:
        results = sorted(
            (result for outcome in outcomes for result in outcome.results),
            key=lambda result: result.criterion.value,
        )
        failures = sorted(
            (outcome.failure for outcome in outcomes if outcome.failure is not None),
            key=lambda failure: failure.evaluator.value,
        )
        observed = {result.criterion for result in results}
        missing = [criterion for criterion in ALL_EVALUATION_CRITERIA if criterion not in observed]
        return CandidateEvaluation(
            candidate_id=candidate_id,
            results=results,
            failures=failures,
            missing_criteria=missing,
            attempt_counts={
                outcome.evaluator.value: outcome.attempts
                for outcome in sorted(
                    outcomes,
                    key=lambda item: item.evaluator.value,
                )
            },
        )


def aggregate_candidate_evaluations(
    evaluations: Sequence[CandidateEvaluation],
) -> list[AggregatedCandidateEvaluation]:
    """Aggregate independent observations without LLM ranking or hidden imputation."""
    _require_unique_ids(
        [evaluation.candidate_id for evaluation in evaluations],
        "candidate_id",
    )
    aggregates: list[AggregatedCandidateEvaluation] = []
    for evaluation in sorted(evaluations, key=lambda item: item.candidate_id):
        scores_by_criterion: dict[EvaluationCriterion, list[float]] = defaultdict(list)
        for result in evaluation.results:
            scores_by_criterion[result.criterion].append(result.score)
        criterion_scores = {
            criterion.value: round(sum(scores) / len(scores), 4)
            for criterion, scores in sorted(
                scores_by_criterion.items(),
                key=lambda item: item[0].value,
            )
        }
        composite_score = (
            round(sum(criterion_scores.values()) / len(criterion_scores), 4)
            if criterion_scores
            else None
        )
        confidence = (
            round(
                sum(result.confidence for result in evaluation.results) / len(evaluation.results),
                4,
            )
            if evaluation.results
            else None
        )
        aggregates.append(
            AggregatedCandidateEvaluation(
                candidate_id=evaluation.candidate_id,
                criterion_scores=criterion_scores,
                composite_score=composite_score,
                confidence=confidence,
                evidence_ids=list(
                    dict.fromkeys(
                        evidence_id
                        for result in evaluation.results
                        for evidence_id in result.evidence_ids
                    )
                ),
                risks=list(
                    dict.fromkeys(risk for result in evaluation.results for risk in result.risks)
                ),
                failures=evaluation.failures,
                missing_criteria=evaluation.missing_criteria,
            )
        )
    return aggregates


def write_evaluation_results(
    artifacts_root: Path,
    run_id: str,
    evaluations: Sequence[CandidateEvaluation],
    aggregates: Sequence[AggregatedCandidateEvaluation],
) -> Path:
    """Write candidate-level results and Python aggregates as a run artifact."""
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a safe path segment")
    run_dir = artifacts_root / "runs" / run_id
    ensure_outside_read_only_data(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "evaluation_results.json"
    payload = {
        "run_id": run_id,
        "candidate_evaluations": [evaluation.model_dump(mode="json") for evaluation in evaluations],
        "aggregated_evaluations": [aggregate.model_dump(mode="json") for aggregate in aggregates],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _require_unique_ids(values: Sequence[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} values must be unique")
