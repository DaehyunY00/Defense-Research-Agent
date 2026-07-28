"""Application and worker tests for deployed research projects."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from defense_research_agent.domain import (
    ResearchBrief,
    ResearchLabReport,
    ResearchLabRun,
    ResearchPlan,
    ResearchRole,
    ResearchTask,
)
from defense_research_agent.domain.research_project import (
    CreateResearchProject,
    ResearchLabReviewDecision,
    ResearchLabReviewSubmission,
    ResearchProjectStatus,
)
from defense_research_agent.repositories.research_projects import (
    InMemoryResearchProjectRepository,
)
from defense_research_agent.services.research_lab import ResearchLabService
from defense_research_agent.services.research_projects import (
    InMemoryResearchRunStore,
    ResearchJobDispatcher,
    ResearchJobDispatchError,
    ResearchProjectApplicationService,
    ResearchProjectRunner,
    ResearchResultIntegrityError,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


class FakeDispatcher(ResearchJobDispatcher):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.project_ids: list[str] = []

    def dispatch(self, project_id: str) -> str:
        self.project_ids.append(project_id)
        if self.fail:
            raise RuntimeError("provider details must remain hidden")
        return f"operations/{project_id}"


class FakeLab:
    def __init__(self, result: ResearchLabRun) -> None:
        self.result = result
        self.briefs: list[ResearchBrief] = []

    def run(self, brief: ResearchBrief) -> ResearchLabRun:
        self.briefs.append(brief)
        return self.result


def _clock() -> Callable[[], datetime]:
    values = iter(NOW + timedelta(seconds=index) for index in range(20))
    return lambda: next(values)


def _request() -> CreateResearchProject:
    return CreateResearchProject(
        question="공개자료로 국방정책 효과를 어떻게 검증할 것인가?",
        objective="검토 가능한 연구설계를 만든다.",
        deliverables=["연구보고서"],
    )


def _run(project_id: str) -> ResearchLabRun:
    brief = _request().to_brief(project_id)
    task = ResearchTask(
        task_id="task:literature",
        role=ResearchRole.LITERATURE_RESEARCHER,
        title="문헌 검토",
        instructions="공개 문헌을 검토한다.",
        expected_output="근거 목록",
    )
    return ResearchLabRun(
        brief=brief,
        plan=ResearchPlan(
            project_id=project_id,
            rationale="전문 역할에 독립 과업을 배정한다.",
            tasks=[task],
            success_criteria=["근거 공백을 명시한다."],
        ),
        final_report=ResearchLabReport(
            project_id=project_id,
            executive_summary="검토 가능한 연구 결과다.",
            human_approval_required=True,
        ),
        execution_order=[
            ResearchRole.MAIN_RESEARCHER,
            ResearchRole.LITERATURE_RESEARCHER,
            ResearchRole.MAIN_RESEARCHER,
        ],
    )


def test_create_dispatch_run_result_and_human_review() -> None:
    repository = InMemoryResearchProjectRepository()
    store = InMemoryResearchRunStore()
    dispatcher = FakeDispatcher()
    application = ResearchProjectApplicationService(
        repository,
        dispatcher,
        store,
        clock=_clock(),
        id_factory=lambda: "research-1",
    )

    created = application.create(_request())

    assert created.status is ResearchProjectStatus.QUEUED
    assert created.execution_name == "operations/research-1"
    assert dispatcher.project_ids == ["research-1"]

    fake_lab = FakeLab(_run("research-1"))
    runner = ResearchProjectRunner(
        repository,
        store,
        lambda: cast(ResearchLabService, fake_lab),
        clock=_clock(),
    )
    completed = runner.run("research-1")

    assert completed is not None
    assert completed.status is ResearchProjectStatus.AWAITING_HUMAN_REVIEW
    assert application.get_result("research-1") == fake_lab.result
    assert fake_lab.briefs == [created.brief]

    reviewed = application.review(
        "research-1",
        ResearchLabReviewSubmission(
            decision=ResearchLabReviewDecision.APPROVE,
            reviewer="human-reviewer",
            comment="검토 완료",
        ),
    )

    assert reviewed.status is ResearchProjectStatus.APPROVED
    assert reviewed.review_history[0].reviewer == "human-reviewer"


def test_held_report_can_receive_a_later_human_decision() -> None:
    repository = InMemoryResearchProjectRepository()
    store = InMemoryResearchRunStore()
    application = ResearchProjectApplicationService(
        repository,
        FakeDispatcher(),
        store,
        clock=_clock(),
        id_factory=lambda: "research-1",
    )
    application.create(_request())
    ResearchProjectRunner(
        repository,
        store,
        lambda: cast(ResearchLabService, FakeLab(_run("research-1"))),
        clock=_clock(),
    ).run("research-1")

    held = application.review(
        "research-1",
        ResearchLabReviewSubmission(
            decision=ResearchLabReviewDecision.HOLD,
            reviewer="reviewer-1",
        ),
    )
    approved = application.review(
        "research-1",
        ResearchLabReviewSubmission(
            decision=ResearchLabReviewDecision.APPROVE,
            reviewer="reviewer-2",
        ),
    )

    assert held.status is ResearchProjectStatus.HELD
    assert approved.status is ResearchProjectStatus.APPROVED
    assert [event.sequence for event in approved.review_history] == [1, 2]


def test_dispatch_failure_is_sanitized_and_persisted() -> None:
    repository = InMemoryResearchProjectRepository()
    application = ResearchProjectApplicationService(
        repository,
        FakeDispatcher(fail=True),
        InMemoryResearchRunStore(),
        clock=_clock(),
        id_factory=lambda: "research-failed",
    )

    with pytest.raises(ResearchJobDispatchError, match="dispatch failed"):
        application.create(_request())

    failed = repository.get("research-failed")
    assert failed is not None
    assert failed.status is ResearchProjectStatus.FAILED
    assert failed.failure_code == "job_dispatch_failed"
    assert "provider details" not in (failed.failure_message or "")


def test_result_checksum_mismatch_is_rejected() -> None:
    repository = InMemoryResearchProjectRepository()
    store = InMemoryResearchRunStore()
    application = ResearchProjectApplicationService(
        repository,
        FakeDispatcher(),
        store,
        clock=_clock(),
        id_factory=lambda: "research-1",
    )
    application.create(_request())
    runner = ResearchProjectRunner(
        repository,
        store,
        lambda: cast(ResearchLabService, FakeLab(_run("research-1"))),
        clock=_clock(),
    )
    runner.run("research-1")
    store.objects["research-projects/research-1/research_lab_run.json"] = b"tampered"

    with pytest.raises(ResearchResultIntegrityError, match="size mismatch"):
        application.get_result("research-1")


def test_runner_skips_a_project_that_is_already_claimed() -> None:
    repository = InMemoryResearchProjectRepository()
    store = InMemoryResearchRunStore()
    application = ResearchProjectApplicationService(
        repository,
        FakeDispatcher(),
        store,
        clock=_clock(),
        id_factory=lambda: "research-1",
    )
    application.create(_request())
    repository.claim("research-1", NOW)
    runner = ResearchProjectRunner(
        repository,
        store,
        lambda: cast(ResearchLabService, FakeLab(_run("research-1"))),
    )

    assert runner.run("research-1") is None
