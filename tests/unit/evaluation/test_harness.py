"""Tests for honest metrics, temporal leakage, and expert-review outputs."""

from pathlib import Path

from defense_research_agent.domain import (
    CandidateAttributes,
    CandidateEvaluation,
    EvaluationCriterion,
    EvaluationFailure,
    EvaluationResult,
    EvaluatorName,
    IngestionReport,
    MetricStatus,
    PublicationType,
    RankedTopic,
    RecommendedOutputType,
    ResearchHorizon,
    ResearchPublication,
    TopicCandidate,
    TopicSignal,
)
from defense_research_agent.evaluation import (
    PilotEvaluationHarness,
    count_temporal_leakage,
    temporal_backtest_summary,
)
from defense_research_agent.repositories import InMemoryResearchPublicationRepository


def _publications() -> list[ResearchPublication]:
    return [
        ResearchPublication(
            publication_id="pub:2024",
            publication_type=PublicationType.DEFENSE_FORUM,
            title="2024년 국방 AI 연구",
            raw_metadata={"_ingestion": {"filename_year": 2024}},
        ),
        ResearchPublication(
            publication_id="pub:2025",
            publication_type=PublicationType.KIDA_BRIEF,
            title="2025년 국방 AI 후속연구",
            raw_metadata={"_ingestion": {"filename_year": 2025}},
        ),
        ResearchPublication(
            publication_id="pub:unknown",
            publication_type=PublicationType.RESEARCH_REPORT,
            title="연도 미상 연구",
        ),
    ]


def _candidate() -> TopicCandidate:
    return TopicCandidate(
        candidate_id="candidate:evaluation",
        working_title="국방 AI 후속 성과평가",
        research_question="기존 연구 이후의 변화를 어떻게 평가할 것인가?",
        recommended_output=RecommendedOutputType.KIDA_BRIEF,
        supporting_signal_ids=["signal:evaluation"],
        related_publication_ids=["pub:2024"],
    )


def _ranked() -> RankedTopic:
    return RankedTopic(
        candidate=_candidate(),
        rank=1,
        criterion_scores={"policy_relevance": 80},
        raw_score=80,
        penalized_score=80,
        adjusted_score=80,
        confidence=0.8,
        evidence_ids=["signal:evaluation", "pub:2024"],
        attributes=CandidateAttributes(
            policy_domains=["국방인공지능"],
            countries=["대한민국"],
            output_type=RecommendedOutputType.KIDA_BRIEF,
            research_horizon=ResearchHorizon.SHORT_TERM,
        ),
        explanation=["원점수는 가중합이다."],
    )


def _ingestion_report() -> IngestionReport:
    return IngestionReport(
        input_path="data",
        publications_path="artifacts/normalized/publications.jsonl",
        total_file_count=4,
        success_count=3,
        failure_count=1,
        skipped_count=0,
        publication_count=3,
        publication_type_counts={"defense_forum": 1},
        suspected_duplicate_count=0,
        suspected_duplicate_group_count=0,
        missing_field_counts={
            "title": 0,
            "authors": 1,
            "publication_date": 3,
        },
    )


def _candidate_evaluation() -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_id="candidate:evaluation",
        results=[
            EvaluationResult(
                candidate_id="candidate:evaluation",
                criterion=EvaluationCriterion.POLICY_RELEVANCE,
                score=80,
                rationale="정책 관련성이 있다.",
                evidence_ids=["pub:2024"],
                confidence=0.8,
            )
        ],
        failures=[
            EvaluationFailure(
                candidate_id="candidate:evaluation",
                evaluator=EvaluatorName.NOVELTY,
                attempts=2,
                error_type="RuntimeError",
                message="fixture failure",
            )
        ],
        missing_criteria=[
            criterion
            for criterion in EvaluationCriterion
            if criterion is not EvaluationCriterion.POLICY_RELEVANCE
        ],
    )


def test_harness_calculates_available_metrics_and_marks_gold_metrics_unavailable(
    tmp_path: Path,
) -> None:
    publications = _publications()
    harness = PilotEvaluationHarness(
        InMemoryResearchPublicationRepository(publications),
        publications,
    )
    signal = TopicSignal(
        signal_id="signal:evaluation",
        signal_type="external_government_policy",
        title="공식 시행계획",
        confidence=0.95,
    )
    summary = harness.evaluate(
        run_id="evaluation-run",
        ingestion_report=_ingestion_report(),
        candidates=[_candidate()],
        signals=[signal],
        candidate_evaluations=[_candidate_evaluation()],
        ranked_topics=[_ranked()],
        cutoff_year=2024,
    )

    parsing = summary.metrics["data_parsing"]
    assert parsing["processing_success_rate"].value == 0.75
    assert parsing["publication_type_classification_accuracy"].status is MetricStatus.UNAVAILABLE
    assert summary.metrics["internal_search"]["recall_at_k"].status is (MetricStatus.UNAVAILABLE)
    assert summary.temporal_backtest.input_publication_count == 1
    assert summary.temporal_backtest.future_target_count == 1
    assert summary.temporal_backtest.unknown_year_count == 1
    assert summary.temporal_backtest.leakage_count == 0
    assert summary.failure_cases

    paths = harness.write_outputs(tmp_path, summary, [_ranked()])

    assert [path.name for path in paths] == [
        "evaluation_summary.json",
        "evaluation_report.md",
        "expert_review_template.csv",
    ]
    assert "unavailable" in paths[1].read_text(encoding="utf-8")
    assert "fixture failure" in paths[1].read_text(encoding="utf-8")
    assert "candidate:evaluation" in paths[2].read_text(encoding="utf-8-sig")


def test_temporal_leakage_detector_reports_future_input() -> None:
    publications = _publications()

    assert count_temporal_leakage(publications, 2024) == 1
    split = temporal_backtest_summary(publications, 2024)
    assert split.leakage_count == 0
    assert split.future_topic_comparison.status is MetricStatus.UNAVAILABLE
