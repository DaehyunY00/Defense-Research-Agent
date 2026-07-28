"""Tests for persistent project and human-review contracts."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import ResearchBrief
from defense_research_agent.domain.research_project import (
    CreateResearchProject,
    ResearchLabReviewDecision,
    ResearchLabReviewEvent,
    ResearchLabReviewSubmission,
    ResearchProjectRecord,
    ResearchProjectStatus,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _brief() -> ResearchBrief:
    return ResearchBrief(
        project_id="research-1",
        question="공개자료로 정책 효과를 어떻게 검증할 것인가?",
        objective="검토 가능한 연구설계를 만든다.",
        deliverables=["연구보고서"],
    )


def test_create_request_binds_server_id_and_rejects_reversed_dates() -> None:
    request = CreateResearchProject(
        question="무엇을 연구할 것인가?",
        objective="연구 범위를 결정한다.",
    )

    assert request.to_brief("server-id").project_id == "server-id"
    assert request.deliverables == ["검토 가능한 연구보고서"]

    with pytest.raises(ValidationError, match="evidence_start_date"):
        CreateResearchProject(
            question="무엇을 연구할 것인가?",
            objective="연구 범위를 결정한다.",
            evidence_start_date=date(2026, 2, 1),
            evidence_end_date=date(2026, 1, 1),
        )


def test_review_submission_requires_edits_only_for_approve_with_edits() -> None:
    with pytest.raises(ValidationError, match="requires at least one"):
        ResearchLabReviewSubmission(
            decision=ResearchLabReviewDecision.APPROVE_WITH_EDITS,
            reviewer="검토자",
        )

    with pytest.raises(ValidationError, match="only with approve_with_edits"):
        ResearchLabReviewSubmission(
            decision=ResearchLabReviewDecision.REJECT,
            reviewer="검토자",
            requested_edits=["표현 수정"],
        )

    with pytest.raises(ValidationError, match="requires requested edits"):
        ResearchLabReviewEvent(
            event_id="review:missing-edits",
            decision=ResearchLabReviewDecision.APPROVE_WITH_EDITS,
            reviewer="검토자",
            reviewed_at=NOW,
            sequence=1,
        )

    with pytest.raises(ValidationError, match="only with approve_with_edits"):
        ResearchLabReviewEvent(
            event_id="review:unexpected-edits",
            decision=ResearchLabReviewDecision.HOLD,
            reviewer="검토자",
            requested_edits=["표현 수정"],
            reviewed_at=NOW,
            sequence=1,
        )


def test_project_record_requires_result_before_review_status() -> None:
    with pytest.raises(ValidationError, match="bound result artifact"):
        ResearchProjectRecord(
            project_id="research-1",
            brief=_brief(),
            status=ResearchProjectStatus.AWAITING_HUMAN_REVIEW,
            created_at=NOW,
            updated_at=NOW,
        )

    with pytest.raises(ValidationError, match="human-review event"):
        ResearchProjectRecord(
            project_id="research-1",
            brief=_brief(),
            status=ResearchProjectStatus.APPROVED,
            created_at=NOW,
            updated_at=NOW,
            result_object="research-projects/research-1/research_lab_run.json",
            result_sha256="1" * 64,
            result_size_bytes=100,
        )

    event = ResearchLabReviewEvent(
        event_id="review:1",
        decision=ResearchLabReviewDecision.APPROVE,
        reviewer="검토자",
        reviewed_at=NOW,
        sequence=1,
    )
    record = ResearchProjectRecord(
        project_id="research-1",
        brief=_brief(),
        status=ResearchProjectStatus.APPROVED,
        created_at=NOW,
        updated_at=NOW,
        result_object="research-projects/research-1/research_lab_run.json",
        result_sha256="1" * 64,
        result_size_bytes=100,
        review_history=[event],
    )

    assert record.human_approval_required is True


def test_review_history_requires_aware_ordered_events_and_matching_status() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ResearchLabReviewEvent(
            event_id="review:naive",
            decision=ResearchLabReviewDecision.HOLD,
            reviewer="검토자",
            reviewed_at=datetime(2026, 7, 28),
            sequence=1,
        )

    held_event = ResearchLabReviewEvent(
        event_id="review:1",
        decision=ResearchLabReviewDecision.HOLD,
        reviewer="검토자",
        reviewed_at=NOW,
        sequence=1,
    )
    approved_event = ResearchLabReviewEvent(
        event_id="review:2",
        decision=ResearchLabReviewDecision.APPROVE,
        reviewer="검토자",
        reviewed_at=NOW + timedelta(seconds=1),
        sequence=2,
    )
    common = {
        "project_id": "research-1",
        "brief": _brief(),
        "created_at": NOW,
        "updated_at": NOW + timedelta(seconds=1),
        "result_object": "research-projects/research-1/research_lab_run.json",
        "result_sha256": "1" * 64,
        "result_size_bytes": 100,
    }

    with pytest.raises(ValidationError, match="contiguous"):
        ResearchProjectRecord.model_validate(
            {
                **common,
                "status": ResearchProjectStatus.APPROVED,
                "review_history": [
                    held_event,
                    approved_event.model_copy(update={"sequence": 3}),
                ],
            }
        )

    with pytest.raises(ValidationError, match="last human-review decision"):
        ResearchProjectRecord.model_validate(
            {
                **common,
                "status": ResearchProjectStatus.HELD,
                "review_history": [held_event, approved_event],
            }
        )

    with pytest.raises(ValidationError, match="only reviewed projects"):
        ResearchProjectRecord.model_validate(
            {
                **common,
                "status": ResearchProjectStatus.AWAITING_HUMAN_REVIEW,
                "review_history": [held_event],
            }
        )
