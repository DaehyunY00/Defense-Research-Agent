"""Offline pilot metrics, temporal leakage checks, and expert-review outputs."""

import csv
import html
import json
import re
from collections.abc import Sequence
from pathlib import Path
from unicodedata import normalize

from defense_research_agent.domain.evaluation import CandidateEvaluation
from defense_research_agent.domain.ingestion import IngestionReport
from defense_research_agent.domain.pilot_evaluation import (
    MetricResult,
    MetricStatus,
    NodeExecutionStatus,
    OrchestrationAudit,
    PilotEvaluationSummary,
    TemporalBacktestSummary,
)
from defense_research_agent.domain.publication import PublicationType, ResearchPublication
from defense_research_agent.domain.ranking import RankedTopic
from defense_research_agent.domain.search import PublicationSearchFilters
from defense_research_agent.domain.topic import TopicCandidate, TopicSignal
from defense_research_agent.path_safety import ensure_outside_read_only_data
from defense_research_agent.repositories.base import ResearchPublicationRepository

_CANONICAL_TEXT_PATTERN = re.compile(r"[^0-9a-z가-힣]+")


class PilotEvaluationHarness:
    """Calculate only evidence-backed metrics and label unavailable metrics honestly."""

    def __init__(
        self,
        repository: ResearchPublicationRepository,
        publications: Sequence[ResearchPublication],
    ) -> None:
        self._repository = repository
        self._publications = tuple(publications)

    def evaluate(
        self,
        *,
        run_id: str,
        ingestion_report: IngestionReport,
        candidates: Sequence[TopicCandidate],
        signals: Sequence[TopicSignal],
        candidate_evaluations: Sequence[CandidateEvaluation],
        ranked_topics: Sequence[RankedTopic],
        cutoff_year: int,
        orchestration_audit: OrchestrationAudit | None = None,
    ) -> PilotEvaluationSummary:
        """Build a complete summary without hiding failures or guessing accuracy."""
        failure_cases = [
            (
                f"{failure.candidate_id}/{failure.evaluator.value}: "
                f"{failure.error_type}: {failure.message}"
            )
            for evaluation in candidate_evaluations
            for failure in evaluation.failures
        ]
        metrics = {
            "data_parsing": self._data_metrics(ingestion_report),
            "internal_search": self._search_metrics(),
            "topic_generation": self._generation_metrics(candidates, signals),
            "evaluation": self._evaluation_metrics(candidate_evaluations),
            "orchestration": self._orchestration_metrics(
                candidate_evaluations,
                orchestration_audit,
            ),
            "expert_output": self._expert_output_metrics(ranked_topics),
        }
        backtest = temporal_backtest_summary(self._publications, cutoff_year)
        return PilotEvaluationSummary(
            run_id=run_id,
            metrics=metrics,
            temporal_backtest=backtest,
            top_candidate_ids=[
                topic.candidate.candidate_id
                for topic in sorted(ranked_topics, key=lambda item: item.rank)[:5]
            ],
            failure_cases=failure_cases,
        )

    def write_outputs(
        self,
        output_dir: Path,
        summary: PilotEvaluationSummary,
        ranked_topics: Sequence[RankedTopic],
    ) -> tuple[Path, Path, Path]:
        """Write JSON, Markdown, and CSV outputs using deterministic ordering."""
        ensure_outside_read_only_data(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / "evaluation_summary.json"
        report_path = output_dir / "evaluation_report.md"
        template_path = output_dir / "expert_review_template.csv"
        summary_path.write_text(
            json.dumps(
                summary.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        report_path.write_text(
            _markdown_report(summary, ranked_topics),
            encoding="utf-8",
        )
        _write_expert_template(template_path, ranked_topics)
        return summary_path, report_path, template_path

    @staticmethod
    def _data_metrics(report: IngestionReport) -> dict[str, MetricResult]:
        process_denominator = report.success_count + report.failure_count
        essential_fields = ("title", "authors", "publication_date")
        missing_total = sum(report.missing_field_counts.get(field, 0) for field in essential_fields)
        essential_denominator = report.publication_count * len(essential_fields)
        return {
            "processing_success_rate": _ratio_metric(
                report.success_count,
                process_denominator,
                "지원 Reader가 처리한 파일 중 성공 비율",
            ),
            "publication_type_classification_accuracy": _unavailable(
                "독립적으로 검수된 자료 유형 골든셋이 없다."
            ),
            "essential_metadata_missing_rate": _ratio_metric(
                missing_total,
                essential_denominator,
                "정규화 title/authors/publication_date의 누락 비율",
            ),
        }

    def _search_metrics(self) -> dict[str, MetricResult]:
        filter_total = 0
        filter_correct = 0
        duplicate_total = 0
        result_total = 0
        for publication_type in PublicationType:
            recent = self._repository.get_recent_publications(1, [publication_type])
            if not recent or not recent[0].title:
                continue
            results = self._repository.search(
                recent[0].title or "",
                PublicationSearchFilters(publication_types=[publication_type]),
                limit=10,
            )
            filter_total += len(results)
            filter_correct += sum(
                result.publication.publication_type is publication_type for result in results
            )
        for query in ("국방", "인공지능", "북한"):
            results = self._repository.search(query, limit=10)
            ids = [result.publication.publication_id for result in results]
            duplicate_total += len(ids) - len(set(ids))
            result_total += len(ids)
        return {
            "recall_at_k": _unavailable("전문가가 승인한 질의별 관련 문서 골든셋이 없다."),
            "publication_type_filter_accuracy": _ratio_metric(
                filter_correct,
                filter_total,
                "유형 필터 결과가 요청 유형과 일치한 비율",
            ),
            "result_duplicate_rate": _ratio_metric(
                duplicate_total,
                result_total,
                "세 고정 질의의 상위 10개 결과 내 publication_id 중복 비율",
            ),
        }

    @staticmethod
    def _generation_metrics(
        candidates: Sequence[TopicCandidate],
        signals: Sequence[TopicSignal],
    ) -> dict[str, MetricResult]:
        total = len(candidates)
        signal_titles = {_canonical_text(signal.title) for signal in signals}
        evidence_count = sum(
            bool(candidate.supporting_signal_ids or candidate.related_publication_ids)
            for candidate in candidates
        )
        internal_link_count = sum(
            bool(candidate.related_publication_ids) for candidate in candidates
        )
        news_title_count = sum(
            _canonical_text(candidate.working_title) in signal_titles for candidate in candidates
        )
        duplicate_count = _duplicate_candidate_count(candidates)
        return {
            "evidence_id_inclusion_rate": _ratio_metric(
                evidence_count,
                total,
                "근거 ID를 하나 이상 포함한 후보 비율",
            ),
            "duplicate_candidate_rate": _ratio_metric(
                duplicate_count,
                total,
                "정규화 제목과 연구질문이 모두 같은 후속 후보 비율",
            ),
            "simple_news_title_rate": _ratio_metric(
                news_title_count,
                total,
                "외부 신호 제목을 그대로 반복한 후보 비율",
            ),
            "internal_publication_link_rate": _ratio_metric(
                internal_link_count,
                total,
                "기존 KIDA publication_id를 포함한 후보 비율",
            ),
        }

    @staticmethod
    def _evaluation_metrics(
        candidate_evaluations: Sequence[CandidateEvaluation],
    ) -> dict[str, MetricResult]:
        result_count = sum(len(evaluation.results) for evaluation in candidate_evaluations)
        failure_count = sum(len(evaluation.failures) for evaluation in candidate_evaluations)
        missing_count = sum(
            len(evaluation.missing_criteria) for evaluation in candidate_evaluations
        )
        expected_count = result_count + missing_count
        return {
            "direct_duplicate_detection_accuracy": _unavailable(
                "직접 중복 여부를 독립 검수한 후보 골든셋이 없다."
            ),
            "official_material_gap_detection_accuracy": _unavailable(
                "공식자료 충분성에 대한 전문가 판정 골든셋이 없다."
            ),
            "schema_success_rate": _ratio_metric(
                result_count,
                result_count + failure_count,
                "검증된 EvaluationResult 수 / 검증 결과와 평가 실패 합계",
            ),
            "criterion_coverage_rate": _ratio_metric(
                result_count,
                expected_count,
                "일곱 평가 기준 중 실제 결과가 존재하는 비율",
            ),
        }

    @staticmethod
    def _orchestration_metrics(
        evaluations: Sequence[CandidateEvaluation],
        audit: OrchestrationAudit | None,
    ) -> dict[str, MetricResult]:
        failure_count = sum(len(evaluation.failures) for evaluation in evaluations)
        recovered_count = sum(
            bool(evaluation.failures and evaluation.results) for evaluation in evaluations
        )
        attempted_statuses = (
            [
                status
                for status in audit.node_statuses.values()
                if status is not NodeExecutionStatus.NOT_RUN
            ]
            if audit is not None
            else []
        )
        successful_statuses = {
            NodeExecutionStatus.SUCCESS,
            NodeExecutionStatus.PARTIAL,
            NodeExecutionStatus.AWAITING_REVIEW,
        }
        return {
            "node_success_rate": (
                _ratio_metric(
                    sum(status in successful_statuses for status in attempted_statuses),
                    len(attempted_statuses),
                    "실행 감사에 기록된 시도 노드 중 성공·부분성공·정상 중단 비율",
                )
                if attempted_statuses
                else _unavailable("실행 산출물에 노드별 상태 기록이 없다.")
            ),
            "partial_failure_recovery_rate": (
                _ratio_metric(
                    recovered_count,
                    sum(bool(evaluation.failures) for evaluation in evaluations),
                    "평가 실패가 있어도 다른 평가 결과가 보존된 후보 비율",
                )
                if failure_count
                else _unavailable("이번 실행에는 부분 실패 사례가 발생하지 않았다.")
            ),
            "retry_count": (
                MetricResult(
                    status=MetricStatus.AVAILABLE,
                    value=audit.retry_count,
                    numerator=audit.retry_count,
                    denominator=1,
                    reason="평가 실행 감사에 기록된 최초 시도 이후 추가 호출 수",
                )
                if audit is not None
                else _unavailable("실행 산출물에 재시도 횟수 기록이 없다.")
            ),
            "interrupt_resume": (
                MetricResult(
                    status=MetricStatus.AVAILABLE,
                    value=int(audit.resumed_after_interrupt),
                    numerator=int(audit.resumed_after_interrupt),
                    denominator=1,
                    reason="같은 run_id의 인간 승인 중단 후 재개 여부",
                )
                if audit is not None and audit.resumed_after_interrupt is not None
                else _unavailable("사람의 실제 승인·재개가 아직 수행되지 않았다.")
            ),
            "reproducibility": (
                MetricResult(
                    status=MetricStatus.AVAILABLE,
                    value=int(audit.reproduction_match),
                    numerator=int(audit.reproduction_match),
                    denominator=1,
                    reason="동일 입력으로 반복 계산한 평가·랭킹 구조 일치 여부",
                )
                if audit is not None and audit.reproduction_match is not None
                else _unavailable("동일 입력 반복 실행 비교 기록이 없다.")
            ),
        }

    @staticmethod
    def _expert_output_metrics(
        ranked_topics: Sequence[RankedTopic],
    ) -> dict[str, MetricResult]:
        top = sorted(ranked_topics, key=lambda item: item.rank)[:5]
        with_evidence = sum(bool(topic.evidence_ids) for topic in top)
        with_trace = sum(bool(topic.criterion_scores and topic.explanation) for topic in top)
        return {
            "top_five_evidence_coverage": _ratio_metric(
                with_evidence,
                len(top),
                "상위 최대 5개 후보 중 근거 ID가 있는 비율",
            ),
            "top_five_score_trace_coverage": _ratio_metric(
                with_trace,
                len(top),
                "상위 최대 5개 후보 중 기준별 점수와 계산 설명이 있는 비율",
            ),
        }


def temporal_backtest_summary(
    publications: Sequence[ResearchPublication],
    cutoff_year: int,
) -> TemporalBacktestSummary:
    """Split solely by known date evidence and verify no future input leakage."""
    inputs: list[ResearchPublication] = []
    targets: list[ResearchPublication] = []
    unknown = 0
    for publication in publications:
        year = effective_publication_year(publication)
        if year is None:
            unknown += 1
        elif year <= cutoff_year:
            inputs.append(publication)
        else:
            targets.append(publication)
    leakage_count = count_temporal_leakage(inputs, cutoff_year)
    return TemporalBacktestSummary(
        cutoff_year=cutoff_year,
        input_publication_count=len(inputs),
        future_target_count=len(targets),
        unknown_year_count=unknown,
        leakage_count=leakage_count,
        future_topic_comparison=_unavailable(
            "기준일 이후 발간물과 생성 후보의 주제 일치 판정 골든셋이 없다."
        ),
    )


def count_temporal_leakage(
    input_publications: Sequence[ResearchPublication],
    cutoff_year: int,
) -> int:
    """Count input records whose best available year is after the cutoff."""
    return sum(
        year > cutoff_year
        for publication in input_publications
        if (year := effective_publication_year(publication)) is not None
    )


def effective_publication_year(publication: ResearchPublication) -> int | None:
    """Return exact publication year or filename-year evidence without inventing dates."""
    if publication.publication_date is not None:
        return publication.publication_date.year
    ingestion = publication.raw_metadata.get("_ingestion")
    if not isinstance(ingestion, dict):
        return None
    year = ingestion.get("filename_year")
    return year if isinstance(year, int) and not isinstance(year, bool) else None


def load_publications(path: Path) -> list[ResearchPublication]:
    """Load normalized UTF-8 JSONL for evaluation."""
    publications: list[ResearchPublication] = []
    with path.open(encoding="utf-8") as source_file:
        for line_number, line in enumerate(source_file, start=1):
            if not line.strip():
                continue
            try:
                publications.append(ResearchPublication.model_validate_json(line))
            except ValueError as error:
                raise ValueError(f"invalid normalized publication on line {line_number}") from error
    return publications


def _ratio_metric(
    numerator: int,
    denominator: int,
    reason: str,
) -> MetricResult:
    if denominator == 0:
        return _unavailable(f"{reason}; 분모가 0이다.")
    return MetricResult(
        status=MetricStatus.AVAILABLE,
        value=round(numerator / denominator, 6),
        numerator=numerator,
        denominator=denominator,
        reason=reason,
    )


def _unavailable(reason: str) -> MetricResult:
    return MetricResult(
        status=MetricStatus.UNAVAILABLE,
        reason=reason,
    )


def _duplicate_candidate_count(candidates: Sequence[TopicCandidate]) -> int:
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for candidate in candidates:
        key = (
            _canonical_text(candidate.working_title),
            _canonical_text(candidate.research_question),
        )
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


def _canonical_text(value: str) -> str:
    return _CANONICAL_TEXT_PATTERN.sub("", normalize("NFC", value).casefold())


def _markdown_report(
    summary: PilotEvaluationSummary,
    ranked_topics: Sequence[RankedTopic],
) -> str:
    lines = [
        "# defense-research-agent 평가 보고서",
        "",
        f"- run_id: `{summary.run_id}`",
        f"- 시점 백테스트 기준 연도: {summary.temporal_backtest.cutoff_year}",
        f"- 입력 발간물: {summary.temporal_backtest.input_publication_count}",
        f"- 미래 평가 대상: {summary.temporal_backtest.future_target_count}",
        f"- 미래 데이터 누출: {summary.temporal_backtest.leakage_count}",
        "",
        "## 지표",
        "",
        "| 영역 | 지표 | 상태 | 값 | 근거·한계 |",
        "|---|---|---|---:|---|",
    ]
    for section, metrics in summary.metrics.items():
        for name, metric in metrics.items():
            value = "unavailable" if metric.value is None else str(metric.value)
            lines.append(
                f"| {section} | {name} | {metric.status.value} | {value} | "
                f"{metric.reason.replace('|', '/')} |"
            )
    lines.extend(["", "## 상위 후보", ""])
    for topic in sorted(ranked_topics, key=lambda item: item.rank)[:5]:
        lines.extend(
            [
                f"### {topic.rank}. {_markdown_safe(topic.candidate.working_title)}",
                "",
                f"- candidate_id: `{topic.candidate.candidate_id}`",
                f"- 연구질문: {_markdown_safe(topic.candidate.research_question)}",
                f"- 원점수 / 조정점수: {topic.raw_score} / {topic.adjusted_score}",
                f"- 근거 ID: {', '.join(topic.evidence_ids) or '없음'}",
                f"- 계산 내역: {'; '.join(topic.explanation)}",
                "",
            ]
        )
    lines.extend(["## 실패 사례", ""])
    if summary.failure_cases:
        lines.extend(f"- {failure}" for failure in summary.failure_cases)
    else:
        lines.append("- 기록된 실패 사례 없음")
    return "\n".join(lines) + "\n"


def _write_expert_template(
    path: Path,
    ranked_topics: Sequence[RankedTopic],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "rank",
                "candidate_id",
                "working_title",
                "research_question",
                "raw_score",
                "adjusted_score",
                "evidence_ids",
                "expert_policy_relevance_1_5",
                "expert_novelty_1_5",
                "expert_feasibility_1_5",
                "decision",
                "edits_or_comments",
            ]
        )
        for topic in sorted(ranked_topics, key=lambda item: item.rank)[:5]:
            writer.writerow(
                [
                    topic.rank,
                    topic.candidate.candidate_id,
                    _csv_safe(topic.candidate.working_title),
                    _csv_safe(topic.candidate.research_question),
                    topic.raw_score,
                    topic.adjusted_score,
                    ";".join(topic.evidence_ids),
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )


def _markdown_safe(value: str) -> str:
    escaped = html.escape(value, quote=True)
    return escaped.replace("|", "\\|")


def _csv_safe(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
