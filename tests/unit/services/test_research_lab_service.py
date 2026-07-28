"""Tests for concurrent research-lab orchestration and partial failure."""

from collections.abc import Sequence
from threading import Barrier

from defense_research_agent.agents import (
    FakeModelGateway,
    MainResearcherAgent,
    ResearchLabAgent,
    build_default_role_specs,
)
from defense_research_agent.domain import (
    ResearchAgentResult,
    ResearchBrief,
    ResearchFinding,
    ResearchLabReport,
    ResearchLabStatus,
    ResearchPlan,
    ResearchRole,
    ResearchRoleSpec,
    ResearchTask,
    ResearchToolContext,
    ToolCapability,
)
from defense_research_agent.services import ResearchLabService, build_review_task_id


class _StubAgent(ResearchLabAgent):
    def __init__(
        self,
        spec: ResearchRoleSpec,
        *,
        barrier: Barrier | None = None,
        fail: bool = False,
    ) -> None:
        self._spec = spec
        self._barrier = barrier
        self._fail = fail
        self.contexts: list[tuple[ResearchAgentResult, ...]] = []

    @property
    def spec(self) -> ResearchRoleSpec:
        return self._spec

    def execute(
        self,
        brief: ResearchBrief,
        task: ResearchTask,
        context_results: Sequence[ResearchAgentResult],
        tool_context: ResearchToolContext,
    ) -> ResearchAgentResult:
        self.contexts.append(tuple(context_results))
        if self._barrier is not None:
            self._barrier.wait(timeout=2)
        if self._fail:
            raise RuntimeError("simulated worker failure")
        return ResearchAgentResult(
            project_id=brief.project_id,
            task_id=task.task_id,
            role=self._spec.role,
            summary=f"{self._spec.role.value} 결과",
            findings=[
                ResearchFinding(
                    finding_id=f"finding:{self._spec.role.value}",
                    statement=f"{self._spec.role.value} 검토 결과",
                    confidence=0.8,
                )
            ],
        )


def _brief() -> ResearchBrief:
    return ResearchBrief(
        project_id="project:service",
        question="국방 AI 정책 집행을 어떻게 연구하는가?",
        objective="연구설계와 PoC 범위를 결정한다.",
        deliverables=["종합 보고서", "PoC 제안"],
    )


def _plan() -> ResearchPlan:
    role_suffixes = {
        ResearchRole.LITERATURE_RESEARCHER: "literature",
        ResearchRole.CURRENT_ISSUE_RESEARCHER: "issues",
        ResearchRole.METHODOLOGY_RESEARCHER: "methods",
        ResearchRole.DEVELOPER_RESEARCHER: "developer",
    }
    return ResearchPlan(
        project_id="project:service",
        rationale="네 전문 분야를 독립적으로 병렬 조사한다.",
        tasks=[
            ResearchTask(
                task_id=f"task:{suffix}",
                role=role,
                title=f"{role.value} 과업",
                instructions="배정된 전문 관점에서 공개자료만 검토한다.",
                expected_output="근거, 한계와 권고",
            )
            for role, suffix in role_suffixes.items()
        ],
        success_criteria=["근거와 반론이 함께 제시된다."],
    )


def _report(source_task_ids: list[str]) -> ResearchLabReport:
    return ResearchLabReport(
        project_id="project:service",
        executive_summary="병렬 전문 연구와 독립 검토를 종합했으며 사람 검토가 필요하다.",
        evidence_gaps=["실제 검색 결과를 연결해야 한다."],
        next_steps=["사람이 결과와 PoC 실행을 승인한다."],
        source_task_ids=source_task_ids,
    )


def _build_service(
    *,
    failed_role: ResearchRole | None = None,
    barrier: Barrier | None = None,
    plan_override: ResearchPlan | None = None,
) -> tuple[ResearchLabService, dict[ResearchRole, _StubAgent]]:
    specs = {spec.role: spec for spec in build_default_role_specs()}
    plan = plan_override or _plan()
    audit_task_id = build_review_task_id(
        "project:service",
        ResearchRole.EVIDENCE_AUDITOR,
    )
    critical_task_id = build_review_task_id(
        "project:service",
        ResearchRole.CRITICAL_REVIEWER,
    )
    successful_task_ids = [task.task_id for task in plan.tasks if task.role is not failed_role]
    source_task_ids = [*successful_task_ids, audit_task_id, critical_task_id]
    coordinator = MainResearcherAgent(
        specs[ResearchRole.MAIN_RESEARCHER],
        FakeModelGateway([plan, _report(source_task_ids)]),
    )
    workers: dict[ResearchRole, _StubAgent] = {
        role: _StubAgent(
            specs[role],
            barrier=barrier if role in {task.role for task in plan.tasks} else None,
            fail=role is failed_role,
        )
        for role in ResearchRole
        if role is not ResearchRole.MAIN_RESEARCHER
    }
    return ResearchLabService(coordinator, workers, max_workers=4), workers


def test_four_specialists_run_concurrently_before_two_independent_reviews() -> None:
    service, workers = _build_service(barrier=Barrier(4))

    run = service.run(_brief())

    assert len(run.specialist_results) == 4
    assert len(run.review_results) == 2
    assert run.failures == []
    assert run.status is ResearchLabStatus.AWAITING_HUMAN_REVIEW
    assert run.final_report.human_approval_required is True
    assert len(run.execution_order) == 8
    assert len(workers[ResearchRole.EVIDENCE_AUDITOR].contexts[0]) == 4
    assert len(workers[ResearchRole.CRITICAL_REVIEWER].contexts[0]) == 4


def test_one_specialist_failure_is_preserved_and_does_not_cancel_synthesis() -> None:
    service, workers = _build_service(failed_role=ResearchRole.DEVELOPER_RESEARCHER)

    run = service.run(_brief())

    assert [result.role for result in run.specialist_results] == [
        ResearchRole.LITERATURE_RESEARCHER,
        ResearchRole.CURRENT_ISSUE_RESEARCHER,
        ResearchRole.METHODOLOGY_RESEARCHER,
    ]
    assert len(run.failures) == 1
    assert run.failures[0].role is ResearchRole.DEVELOPER_RESEARCHER
    assert run.failures[0].error_type == "RuntimeError"
    assert run.failures[0].message == "RuntimeError: research agent execution failed"
    assert len(workers[ResearchRole.EVIDENCE_AUDITOR].contexts[0]) == 3
    assert run.final_report.project_id == "project:service"


def test_tool_request_outside_role_allow_list_becomes_a_scoped_failure() -> None:
    plan = _plan()
    denied_task = plan.tasks[0].model_copy(
        update={"requested_tools": [ToolCapability.CODE_SANDBOX]}
    )
    denied_plan = plan.model_copy(update={"tasks": [denied_task, *plan.tasks[1:]]})
    service, _ = _build_service(
        failed_role=ResearchRole.LITERATURE_RESEARCHER,
        plan_override=denied_plan,
    )

    run = service.run(_brief())

    failure = next(item for item in run.failures if item.role is ResearchRole.LITERATURE_RESEARCHER)
    tool_context = next(
        item for item in run.tool_contexts if item.role is ResearchRole.LITERATURE_RESEARCHER
    )
    assert failure.error_type == "ResearchToolPolicyError"
    assert tool_context.failures[0].code == "tool_not_allowed"


def test_missing_code_sandbox_is_recorded_without_discarding_developer_result() -> None:
    plan = _plan()
    developer_tasks = [
        task.model_copy(update={"requested_tools": [ToolCapability.CODE_SANDBOX]})
        if task.role is ResearchRole.DEVELOPER_RESEARCHER
        else task
        for task in plan.tasks
    ]
    service, _ = _build_service(plan_override=plan.model_copy(update={"tasks": developer_tasks}))

    run = service.run(_brief())

    assert any(
        result.role is ResearchRole.DEVELOPER_RESEARCHER for result in run.specialist_results
    )
    developer_context = next(
        context
        for context in run.tool_contexts
        if context.role is ResearchRole.DEVELOPER_RESEARCHER
    )
    assert developer_context.failures[0].code == "code_sandbox_unavailable"
    assert run.sandbox_results == []
