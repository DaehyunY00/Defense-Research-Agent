"""Tests for human decisions, append-only history, and planning-card gates."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import (
    CandidateAttributes,
    PublicationType,
    RankedTopic,
    RecommendedOutputType,
    ResearchHorizon,
    ResearchPublication,
    ReviewDecisionType,
    ReviewEdits,
    ReviewSubmission,
    ReviewWorkflowStatus,
    TopicCandidate,
    TopicSignal,
)
from defense_research_agent.repositories import (
    InMemoryResearchPublicationRepository,
    ReviewHistoryRepository,
)
from defense_research_agent.services.review import HumanReviewService

FIXED_TIME = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _ranked_topic() -> RankedTopic:
    candidate = TopicCandidate(
        candidate_id="candidate:review",
        working_title="국방 AI 정책 성과평가",
        research_question="공개자료로 정책 성과를 어떻게 평가할 것인가?",
        trigger="공식 시행계획 공개",
        novelty_claim="기존 연구 이후 집행 성과를 검토한다.",
        recommended_output=RecommendedOutputType.DEFENSE_POLICY_RESEARCH,
        supporting_signal_ids=["signal:review"],
        related_publication_ids=["pub:forum", "pub:brief", "pub:policy"],
        known_limitations=["비공개 성과자료가 제한된다."],
    )
    return RankedTopic(
        candidate=candidate,
        rank=1,
        criterion_scores={"public_evidence_sufficiency": 72},
        raw_score=80,
        penalized_score=80,
        adjusted_score=80,
        confidence=0.8,
        evidence_ids=[
            "signal:review",
            "pub:forum",
            "pub:brief",
            "pub:policy",
        ],
        attributes=CandidateAttributes(
            policy_domains=["국방인공지능"],
            countries=["대한민국"],
            output_type=RecommendedOutputType.DEFENSE_POLICY_RESEARCH,
            research_horizon=ResearchHorizon.STRUCTURAL,
        ),
        explanation=["가중합 80점"],
    )


def _service(tmp_path: Path) -> HumanReviewService:
    repository = InMemoryResearchPublicationRepository(
        [
            ResearchPublication(
                publication_id="pub:forum",
                publication_type=PublicationType.DEFENSE_FORUM,
                title="관련 국방논단",
            ),
            ResearchPublication(
                publication_id="pub:brief",
                publication_type=PublicationType.KIDA_BRIEF,
                title="관련 KIDA Brief",
            ),
            ResearchPublication(
                publication_id="pub:policy",
                publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
                title="관련 국방정책연구",
            ),
        ]
    )
    history = ReviewHistoryRepository(tmp_path, clock=lambda: FIXED_TIME)
    signal = TopicSignal(
        signal_id="signal:review",
        signal_type="external_government_policy",
        title="공식 시행계획",
        confidence=0.95,
    )
    return HumanReviewService(history, repository, [signal])


def _submission(
    decision: ReviewDecisionType,
    *,
    edits: ReviewEdits | None = None,
) -> ReviewSubmission:
    return ReviewSubmission(
        candidate_id="candidate:review",
        decision=decision,
        reviewer="연구자",
        edits=edits,
        comment="검토 의견",
    )


def test_approve_generates_card_with_all_internal_publication_types(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ranked = [_ranked_topic()]
    service.record_decision("review-run", ranked, _submission(ReviewDecisionType.APPROVE))

    gate = service.review_gate("review-run", ranked)
    cards = service.generate_planning_cards("review-run", ranked)
    output_path = service.write_planning_cards(tmp_path, "review-run", ranked)

    assert gate.status is ReviewWorkflowStatus.READY_FOR_CARDS
    assert len(cards) == 1
    assert cards[0].related_defense_forum[0].evidence_id == "pub:forum"
    assert cards[0].related_kida_brief[0].evidence_id == "pub:brief"
    assert cards[0].related_defense_policy_research[0].evidence_id == "pub:policy"
    assert cards[0].external_evidence[0].title == "공식 시행계획"
    assert output_path is not None and output_path.is_file()


def test_approve_with_edits_applies_researcher_fields(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ranked = [_ranked_topic()]
    service.record_decision(
        "edit-run",
        ranked,
        _submission(
            ReviewDecisionType.APPROVE_WITH_EDITS,
            edits=ReviewEdits(
                working_title="연구자가 수정한 가제",
                research_question="수정된 핵심 연구질문은 무엇인가?",
            ),
        ),
    )

    card = service.generate_planning_cards("edit-run", ranked)[0]

    assert card.working_title == "연구자가 수정한 가제"
    assert card.research_question == "수정된 핵심 연구질문은 무엇인가?"


def test_review_decision_rejects_empty_or_misclassified_edits() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ReviewEdits()

    with pytest.raises(ValidationError, match="only with approve_with_edits"):
        ReviewSubmission(
            candidate_id="candidate:review",
            decision=ReviewDecisionType.APPROVE,
            reviewer="연구자",
            edits=ReviewEdits(working_title="무시되면 안 되는 수정"),
        )


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [
        (ReviewDecisionType.HOLD, ReviewWorkflowStatus.AWAITING_REVIEW),
        (
            ReviewDecisionType.REJECT,
            ReviewWorkflowStatus.COMPLETED_WITHOUT_APPROVAL,
        ),
    ],
)
def test_hold_and_reject_never_generate_final_cards(
    tmp_path: Path,
    decision: ReviewDecisionType,
    expected_status: ReviewWorkflowStatus,
) -> None:
    service = _service(tmp_path)
    ranked = [_ranked_topic()]
    service.record_decision("blocked-run", ranked, _submission(decision))

    assert service.review_gate("blocked-run", ranked).status is expected_status
    assert service.generate_planning_cards("blocked-run", ranked) == []
    assert service.write_planning_cards(tmp_path, "blocked-run", ranked) is None
    assert not (tmp_path / "runs" / "blocked-run" / "topic_planning_cards.json").exists()


def test_invalid_candidate_is_rejected_before_history_append(tmp_path: Path) -> None:
    service = _service(tmp_path)
    invalid = ReviewSubmission(
        candidate_id="candidate:unknown",
        decision=ReviewDecisionType.APPROVE,
        reviewer="연구자",
    )

    with pytest.raises(ValueError, match="unknown candidate_id"):
        service.record_decision("invalid-run", [_ranked_topic()], invalid)

    assert not (tmp_path / "runs" / "invalid-run" / "review_history.jsonl").exists()


def test_history_is_append_only_and_latest_decision_resumes_same_run(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ranked = [_ranked_topic()]
    service.record_decision(
        "resume-run",
        ranked,
        _submission(ReviewDecisionType.HOLD),
    )
    history_path = tmp_path / "runs" / "resume-run" / "review_history.jsonl"
    first_bytes = history_path.read_bytes()

    service.record_decision(
        "resume-run",
        ranked,
        _submission(ReviewDecisionType.APPROVE),
    )
    events = ReviewHistoryRepository(tmp_path).load("resume-run")

    assert history_path.read_bytes().startswith(first_bytes)
    assert [event.sequence for event in events] == [1, 2]
    assert service.review_gate("resume-run", ranked).status is (
        ReviewWorkflowStatus.READY_FOR_CARDS
    )


def test_history_serializes_concurrent_appends_without_losing_events(
    tmp_path: Path,
) -> None:
    repository = ReviewHistoryRepository(tmp_path, clock=lambda: FIXED_TIME)

    def append(index: int) -> None:
        repository.append(
            "concurrent-run",
            ReviewSubmission(
                candidate_id="candidate:review",
                decision=ReviewDecisionType.HOLD,
                reviewer=f"연구자-{index}",
                comment=f"동시 검토 {index}",
            ),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(24)))

    events = repository.load("concurrent-run")
    assert [event.sequence for event in events] == list(range(1, 25))
    assert {event.comment for event in events} == {f"동시 검토 {index}" for index in range(24)}
