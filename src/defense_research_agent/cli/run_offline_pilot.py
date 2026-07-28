"""Run a deterministic FakeModelGateway pilot up to the human review gate."""

import argparse
import json
import re
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import cast

from defense_research_agent.agents import (
    EvidenceFeasibilityEvaluator,
    FakeModelGateway,
    NoveltyEvaluator,
    OutputFitEvaluator,
    PolicyRelevanceEvaluator,
    TopicCandidateEvaluator,
)
from defense_research_agent.domain import (
    EvaluationCriterion,
    PublicationSearchResult,
    RecommendedOutputType,
    TopicCandidate,
    TopicGeneratorInput,
    TopicSignal,
)
from defense_research_agent.issues import MockExternalIssueSearchProvider
from defense_research_agent.repositories import InMemoryResearchPublicationRepository
from defense_research_agent.services.evaluation import (
    EvaluationRunner,
    aggregate_candidate_evaluations,
    write_evaluation_results,
)
from defense_research_agent.services.external_issues import ExternalIssueNormalizationService
from defense_research_agent.services.ranking import (
    diversify_candidates,
    load_ranking_config,
    rank_candidates,
    write_ranked_candidates,
)
from defense_research_agent.services.topic_generator import TopicGenerator

_TEMPLATES = (
    (
        "국방 AI 인력정책의 집행성과 측정체계",
        "기존 국방 AI 연구 이후 인력정책 집행성과를 공개자료로 어떻게 측정할 것인가?",
        "기술 도입 논의에서 인력정책 집행지표와 환류 구조로 분석 범위를 확장한다.",
        RecommendedOutputType.DEFENSE_POLICY_RESEARCH,
    ),
    (
        "국방 AI 사업의 예산-성과 연계 점검방안",
        "최근 공식 점검자료를 기존 예산 연구와 연결해 사업 성과를 어떻게 검증할 것인가?",
        "예산 투입과 공개 성과지표 사이의 추적 가능한 연결을 후속 연구로 제안한다.",
        RecommendedOutputType.KIDA_BRIEF,
    ),
    (
        "첨단무기 획득사업 감사결과의 제도개선 함의",
        "최근 감사결과는 기존 획득정책 연구의 어떤 제도 공백을 보여주는가?",
        "개별 감사 요약을 넘어 반복 가능한 획득사업 위험 통제체계를 분석한다.",
        RecommendedOutputType.DEFENSE_FORUM,
    ),
    (
        "동맹 국방 AI 인력준비태세 비교의 한국 정책 적용조건",
        "동맹국 인력준비태세 논의를 한국 국방 인력정책에 적용하려면 어떤 조건이 필요한가?",
        "단순 해외사례 소개가 아니라 국내 제도 적용조건과 공개자료 한계를 검증한다.",
        RecommendedOutputType.RESEARCH_REPORT,
    ),
    (
        "군사 AI 알고리즘 공급망 정책의 후속 쟁점",
        "기존 알고리즘 공급망 연구 이후 최근 정책 변화가 새롭게 제기한 쟁점은 무엇인가?",
        "선행연구 이후 인력·예산·공급망 책임구조의 변화를 함께 검토한다.",
        RecommendedOutputType.DEFENSE_FORUM,
    ),
)
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SIGNAL_TITLE_MARKERS = (
    "인력정책 추진계획",
    "예산 및 성과점검",
    "감사결과",
    "Allied Defense",
    "공급망 정책 쟁점",
)
_PUBLICATION_TITLE_MARKERS = (
    "군사인공지능개발",
    "국방예산분석",
    "국방과학기술정책",
    "인공지능기반",
    "군사인공지능개발",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m defense_research_agent.cli.run_offline_pilot",
        description="Create an illustrative offline run and stop before human approval.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--normalized",
        type=Path,
        default=Path("artifacts/normalized/publications.jsonl"),
    )
    parser.add_argument(
        "--external-fixture",
        type=Path,
        default=Path("tests/fixtures/external_issues.json"),
    )
    parser.add_argument(
        "--scoring-config",
        type=Path,
        default=Path("configs/scoring.json"),
    )
    parser.add_argument("--artifacts-root", type=Path, default=Path("artifacts"))
    parser.add_argument("--query", default="국방 인공지능 정책")
    parser.add_argument("--candidate-count", type=int, default=5)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Generate, evaluate, rank, and stop in an explicit awaiting-review state."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    run_id = cast(str, args.run_id)
    normalized_path = cast(Path, args.normalized)
    fixture_path = cast(Path, args.external_fixture)
    config_path = cast(Path, args.scoring_config)
    artifacts_root = cast(Path, args.artifacts_root)
    query = cast(str, args.query)
    candidate_count = cast(int, args.candidate_count)
    if not _SAFE_RUN_ID.fullmatch(run_id):
        parser.error("--run-id must be a safe path segment")
    if not 1 <= candidate_count <= len(_TEMPLATES):
        parser.error(f"--candidate-count must be between 1 and {len(_TEMPLATES)}")
    for required_path in (normalized_path, fixture_path, config_path):
        if not required_path.is_file():
            parser.error(f"required input does not exist: {required_path}")

    try:
        repository = InMemoryResearchPublicationRepository.from_jsonl(normalized_path)
        internal_results = repository.search(
            query,
            limit=max(candidate_count, 10),
        )
        if not internal_results:
            raise ValueError("internal search returned no publications for the pilot query")
        provider = MockExternalIssueSearchProvider(fixture_path)
        issue_result = provider.search_recent_issues_with_status(
            "",
            None,
            None,
            [],
            100,
        )
        normalized_issues = ExternalIssueNormalizationService().normalize_search_result(
            issue_result
        )
        if not normalized_issues.topic_signals:
            raise ValueError("external fixture returned no valid topic signals")
        generator_input = TopicGeneratorInput(
            normalized_signals=normalized_issues.topic_signals,
            internal_search_results=internal_results,
            existing_publication_types=list(
                dict.fromkeys(result.publication.publication_type for result in internal_results)
            ),
            user_interest_domains=["국방인공지능", "국방획득"],
            excluded_domains=[],
            candidate_count=candidate_count,
        )
        draft_response = {
            "candidates": _candidate_drafts(
                internal_results,
                normalized_issues.topic_signals,
                candidate_count,
            )
        }
        candidates = TopicGenerator(FakeModelGateway([draft_response])).generate(generator_input)
        evaluators = _fake_evaluators(candidates)
        runner = EvaluationRunner(
            evaluators,
            repository,
            max_retries=0,
            max_workers=1,
        )
        evaluations = runner.evaluate(candidates, normalized_issues.topic_signals)
        aggregates = aggregate_candidate_evaluations(evaluations)
        evaluation_path = write_evaluation_results(
            artifacts_root,
            run_id,
            evaluations,
            aggregates,
        )
        config = load_ranking_config(config_path)
        ranked = diversify_candidates(
            rank_candidates(
                candidates,
                aggregates,
                normalized_issues.topic_signals,
                config,
            ),
            config,
            limit=candidate_count,
        )
        repeated_ranked = diversify_candidates(
            rank_candidates(
                candidates,
                aggregates,
                normalized_issues.topic_signals,
                config,
            ),
            config,
            limit=candidate_count,
        )
        reproduction_match = [topic.model_dump_json() for topic in ranked] == [
            topic.model_dump_json() for topic in repeated_ranked
        ]
        retry_count = sum(
            max(0, attempts - 1)
            for evaluation in evaluations
            for attempts in evaluation.attempt_counts.values()
        )
        ranking_path = write_ranked_candidates(
            artifacts_root,
            run_id,
            ranked,
            config,
        )
        state_path = _write_pending_state(
            artifacts_root,
            run_id,
            query,
            candidates,
            normalized_issues.search_status.value,
            retry_count,
            reproduction_match,
            any(evaluation.failures for evaluation in evaluations),
        )
    except ValueError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                "run_id": run_id,
                "mode": "offline_fake_model_example",
                "status": "awaiting_review",
                "candidate_count": len(candidates),
                "evaluation_results": str(evaluation_path),
                "ranked_candidates": str(ranking_path),
                "run_state": str(state_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _candidate_drafts(
    publications: Sequence[PublicationSearchResult],
    signals: Sequence[TopicSignal],
    count: int,
) -> list[dict[str, object]]:
    drafts: list[dict[str, object]] = []
    for index, template in enumerate(_TEMPLATES[:count]):
        title, question, novelty, output = template
        publication = next(
            (
                result.publication
                for result in publications
                if _PUBLICATION_TITLE_MARKERS[index] in (result.publication.title or "")
            ),
            publications[index % len(publications)].publication,
        )
        signal = next(
            (
                candidate_signal
                for candidate_signal in signals
                if _SIGNAL_TITLE_MARKERS[index] in candidate_signal.title
            ),
            signals[index % len(signals)],
        )
        drafts.append(
            {
                "working_title": title,
                "research_question": question,
                "trigger": f"{signal.title} 공개 이후 정책 검토 필요성이 커졌다.",
                "internal_context": (
                    f"기존 자료 '{publication.title or publication.publication_id}'의 "
                    "분석 범위와 한계를 후속 검토한다."
                ),
                "novelty_claim": novelty,
                "recommended_output": output.value,
                "supporting_signal_ids": [signal.signal_id],
                "related_publication_ids": [publication.publication_id],
                "known_limitations": [
                    "공개자료만으로 비공개 사업 성과와 내부 의사결정은 검증하기 어렵다."
                ],
            }
        )
    return drafts


def _fake_evaluators(
    candidates: Sequence[TopicCandidate],
) -> list[TopicCandidateEvaluator]:
    ordered = sorted(candidates, key=lambda candidate: candidate.candidate_id)
    return [
        PolicyRelevanceEvaluator(
            FakeModelGateway(
                [
                    _evaluation_response(
                        candidate,
                        (
                            EvaluationCriterion.POLICY_RELEVANCE,
                            EvaluationCriterion.TIMELINESS,
                            EvaluationCriterion.POLICY_IMPACT,
                        ),
                    )
                    for candidate in ordered
                ]
            )
        ),
        NoveltyEvaluator(
            FakeModelGateway(
                [
                    _evaluation_response(
                        candidate,
                        (EvaluationCriterion.NOVELTY,),
                    )
                    for candidate in ordered
                ]
            )
        ),
        EvidenceFeasibilityEvaluator(
            FakeModelGateway(
                [
                    _evaluation_response(
                        candidate,
                        (
                            EvaluationCriterion.PUBLIC_EVIDENCE_SUFFICIENCY,
                            EvaluationCriterion.FEASIBILITY,
                        ),
                    )
                    for candidate in ordered
                ]
            )
        ),
        OutputFitEvaluator(
            FakeModelGateway(
                [
                    _evaluation_response(
                        candidate,
                        (EvaluationCriterion.OUTPUT_FIT,),
                    )
                    for candidate in ordered
                ]
            )
        ),
    ]


def _evaluation_response(
    candidate: TopicCandidate,
    criteria: Sequence[EvaluationCriterion],
) -> dict[str, object]:
    offset = int(sha256(candidate.candidate_id.encode()).hexdigest()[:2], 16) % 9 - 4
    base_scores = {
        EvaluationCriterion.POLICY_RELEVANCE: 82,
        EvaluationCriterion.TIMELINESS: 80,
        EvaluationCriterion.POLICY_IMPACT: 78,
        EvaluationCriterion.NOVELTY: 76,
        EvaluationCriterion.PUBLIC_EVIDENCE_SUFFICIENCY: 74,
        EvaluationCriterion.FEASIBILITY: 72,
        EvaluationCriterion.OUTPUT_FIT: 80,
    }
    evidence_ids = [
        *candidate.supporting_signal_ids,
        *candidate.related_publication_ids,
    ]
    return {
        "results": [
            {
                "candidate_id": candidate.candidate_id,
                "criterion": criterion.value,
                "score": base_scores[criterion] + offset,
                "rationale": (
                    "오프라인 FakeModelGateway 예시 평가이며 실제 전문가 성능 수치가 아니다."
                ),
                "evidence_ids": evidence_ids,
                "risks": ["illustrative_fake_evaluation"],
                "confidence": 0.7,
            }
            for criterion in criteria
        ]
    }


def _write_pending_state(
    artifacts_root: Path,
    run_id: str,
    query: str,
    candidates: Sequence[TopicCandidate],
    external_search_status: str,
    retry_count: int,
    reproduction_match: bool,
    has_evaluation_failures: bool,
) -> Path:
    output_path = artifacts_root / "runs" / run_id / "run_state.json"
    output_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": "offline_fake_model_example",
                "status": "awaiting_review",
                "human_approved": False,
                "query": query,
                "external_search_status": external_search_status,
                "candidate_ids": [candidate.candidate_id for candidate in candidates],
                "warning": "평가 점수는 FakeModelGateway 예시이며 실제 성능 수치가 아니다.",
                "orchestration_audit": {
                    "node_statuses": {
                        "generate_topic_candidates": "success",
                        "parallel_evaluations": (
                            "partial" if has_evaluation_failures else "success"
                        ),
                        "aggregate_evaluations": "success",
                        "rank_candidates": "success",
                        "diversify_candidates": "success",
                        "human_review_interrupt": "awaiting_review",
                        "generate_topic_planning_cards": "not_run",
                    },
                    "retry_count": retry_count,
                    "resumed_after_interrupt": None,
                    "reproduction_match": reproduction_match,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


if __name__ == "__main__":
    raise SystemExit(main())
