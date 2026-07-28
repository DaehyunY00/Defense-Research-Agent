"""Offline end-to-end graph tests through human pause and same-run resume."""

import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast
from unicodedata import normalize

from defense_research_agent.agents import (
    EvidenceFeasibilityEvaluator,
    FakeModelGateway,
    NoveltyEvaluator,
    OutputFitEvaluator,
    PolicyRelevanceEvaluator,
)
from defense_research_agent.domain import (
    PublicationSearchResult,
    PublicationType,
    ResearchPublication,
    ReviewDecisionType,
    ReviewSubmission,
    ReviewWorkflowStatus,
    SearchField,
    TopicSignal,
)
from defense_research_agent.graph import (
    ResearchWorkflowState,
    build_research_workflow_graph,
)
from defense_research_agent.repositories import (
    InMemoryResearchPublicationRepository,
    ReviewHistoryRepository,
)
from defense_research_agent.services.evaluation import EvaluationRunner
from defense_research_agent.services.ranking import load_ranking_config
from defense_research_agent.services.review import HumanReviewService
from defense_research_agent.services.topic_generator import TopicGenerator

PROJECT_ROOT = Path(__file__).parents[2]
FIXED_TIME = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
TITLE = "국방 AI 집행 성과평가 체계"
QUESTION = "기존 연구 이후 공개된 시행계획의 성과를 어떻게 평가할 것인가?"


def _candidate_id() -> str:
    def canonical(value: str) -> str:
        return re.sub(
            r"[^0-9a-z가-힣]+",
            "",
            normalize("NFC", value).casefold(),
        )

    identity = "\0".join(
        (
            canonical(TITLE),
            canonical(QUESTION),
            "signal:workflow",
            "pub:workflow",
        )
    )
    return f"candidate:{sha256(identity.encode()).hexdigest()[:24]}"


def _topic_response() -> dict[str, object]:
    return {
        "candidates": [
            {
                "working_title": TITLE,
                "research_question": QUESTION,
                "trigger": "공식 시행계획이 공개되어 집행 성과 검토가 필요하다.",
                "internal_context": "기존 국방 AI 정책연구를 후속 검토한다.",
                "novelty_claim": "집행 이후의 성과지표와 환류 체계를 새롭게 제시한다.",
                "recommended_output": "국방정책연구",
                "supporting_signal_ids": ["signal:workflow"],
                "related_publication_ids": ["pub:workflow"],
                "known_limitations": ["비공개 사업자료는 확인하기 어렵다."],
            }
        ]
    }


def _evaluation_response(criteria: tuple[str, ...]) -> dict[str, object]:
    return {
        "results": [
            {
                "candidate_id": _candidate_id(),
                "criterion": criterion,
                "score": 80,
                "rationale": "공개 근거와 정책 문제를 연결했다.",
                "evidence_ids": ["signal:workflow", "pub:workflow"],
                "risks": [],
                "confidence": 0.8,
            }
            for criterion in criteria
        ]
    }


def _state(publication: ResearchPublication) -> ResearchWorkflowState:
    signal = TopicSignal(
        signal_id="signal:workflow",
        signal_type="external_government_policy",
        title="국방 AI 공식 시행계획",
        policy_domains=["국방인공지능"],
        countries=["대한민국"],
        confidence=0.95,
        source_ids=["source:workflow"],
        raw_metadata={"external_source": {"reliability_tier": "tier_1_official"}},
    )
    return {
        "run_id": "graph-pilot",
        "normalized_signals": [signal],
        "internal_search_results": [
            PublicationSearchResult(
                publication=publication,
                score=10,
                matched_fields=[SearchField.TITLE],
                matched_terms=["국방", "AI"],
            )
        ],
        "existing_publication_types": [publication.publication_type],
        "user_interest_domains": ["국방인공지능"],
        "excluded_domains": [],
        "candidate_count": 1,
    }


def test_full_graph_pauses_before_approval_and_resumes_same_run(
    tmp_path: Path,
) -> None:
    publication = ResearchPublication(
        publication_id="pub:workflow",
        publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
        title="기존 국방 AI 정책 기반 연구",
    )
    repository = InMemoryResearchPublicationRepository([publication])
    topic_gateway = FakeModelGateway([_topic_response()])
    evaluators = [
        PolicyRelevanceEvaluator(
            FakeModelGateway(
                [_evaluation_response(("policy_relevance", "timeliness", "policy_impact"))]
            )
        ),
        NoveltyEvaluator(FakeModelGateway([_evaluation_response(("novelty",))])),
        EvidenceFeasibilityEvaluator(
            FakeModelGateway([_evaluation_response(("public_evidence_sufficiency", "feasibility"))])
        ),
        OutputFitEvaluator(FakeModelGateway([_evaluation_response(("output_fit",))])),
    ]
    runner = EvaluationRunner(evaluators, repository, max_retries=0)
    history = ReviewHistoryRepository(tmp_path, clock=lambda: FIXED_TIME)
    review_service = HumanReviewService(
        history,
        repository,
        _state(publication)["normalized_signals"],
    )
    graph = build_research_workflow_graph(
        TopicGenerator(topic_gateway),
        runner,
        load_ranking_config(PROJECT_ROOT / "configs" / "scoring.json"),
        review_service,
        tmp_path,
    )

    paused = graph.invoke(_state(publication))

    assert paused["review_status"] is ReviewWorkflowStatus.AWAITING_REVIEW
    assert "topic_planning_cards" not in paused
    assert (tmp_path / "runs" / "graph-pilot" / "evaluation_results.json").is_file()
    assert (tmp_path / "runs" / "graph-pilot" / "ranked_candidates.json").is_file()
    assert not (tmp_path / "runs" / "graph-pilot" / "topic_planning_cards.json").exists()
    assert len(topic_gateway.calls) == 1
    assert {
        "generate_topic_candidates",
        "parallel_evaluations",
        "aggregate_evaluations",
        "rank_candidates",
        "diversify_candidates",
        "human_review_interrupt",
        "generate_topic_planning_cards",
    } <= set(graph.get_graph().nodes)

    resume_state = cast(ResearchWorkflowState, paused)
    resume_state["review_submissions"] = [
        ReviewSubmission(
            candidate_id=_candidate_id(),
            decision=ReviewDecisionType.APPROVE,
            reviewer="테스트 연구자",
        )
    ]
    completed = graph.invoke(resume_state)

    assert completed["review_status"] is ReviewWorkflowStatus.READY_FOR_CARDS
    assert completed["topic_planning_cards"][0].candidate_id == _candidate_id()
    assert len(topic_gateway.calls) == 1
    assert (tmp_path / "runs" / "graph-pilot" / "topic_planning_cards.json").is_file()
    assert len(history.load("graph-pilot")) == 1
