"""Deterministic orchestration for a seven-role research lab."""

from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from pydantic import JsonValue

from defense_research_agent.agents.research_lab import (
    MainResearcherAgent,
    ResearchLabAgent,
    required_worker_roles,
)
from defense_research_agent.domain.research_lab import (
    REVIEW_ROLES,
    ArtifactKind,
    CodeSandboxResult,
    CodeSandboxStatus,
    ResearchAgentFailure,
    ResearchAgentResult,
    ResearchBrief,
    ResearchLabRun,
    ResearchRole,
    ResearchTask,
    ResearchToolContext,
    ResearchToolEvidence,
    ResearchToolFailure,
    ToolCapability,
)
from defense_research_agent.services.code_sandbox import CodeSandboxExecutor
from defense_research_agent.services.research_tools import (
    ResearchToolRuntime,
    empty_tool_context,
    unavailable_tool_context,
)


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    task: ResearchTask
    tool_context: ResearchToolContext
    result: ResearchAgentResult | None = None
    failure: ResearchAgentFailure | None = None
    sandbox_results: tuple[CodeSandboxResult, ...] = ()


class ResearchToolPolicyError(ValueError):
    """Raised when a plan requests a tool outside the assigned role allow-list."""


class ResearchLabService:
    """Run planning, parallel specialist work, independent review, and synthesis."""

    def __init__(
        self,
        main_researcher: MainResearcherAgent,
        workers: Mapping[ResearchRole, ResearchLabAgent],
        *,
        max_workers: int = 4,
        tool_runtime: ResearchToolRuntime | None = None,
        code_sandbox: CodeSandboxExecutor | None = None,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        expected_roles = set(required_worker_roles())
        supplied_roles = set(workers)
        if supplied_roles != expected_roles:
            missing = expected_roles - supplied_roles
            extra = supplied_roles - expected_roles
            raise ValueError(
                "workers must cover exactly the six non-main roles; "
                f"missing={sorted(role.value for role in missing)}, "
                f"extra={sorted(role.value for role in extra)}"
            )
        for role, worker in workers.items():
            if worker.spec.role is not role:
                raise ValueError(f"worker mapping key {role.value} does not match its role spec")
        self._main_researcher = main_researcher
        self._workers = dict(workers)
        self._max_workers = max_workers
        self._tool_runtime = tool_runtime
        self._code_sandbox = code_sandbox

    def run(self, brief: ResearchBrief) -> ResearchLabRun:
        """Execute one complete run and stop at the human-review boundary."""
        plan = self._main_researcher.plan(brief)
        specialist_outcomes = self._run_stage(brief, plan.tasks, ())
        (
            specialist_results,
            specialist_failures,
            specialist_tool_contexts,
            specialist_sandbox_results,
        ) = self._split_outcomes(specialist_outcomes)

        review_tasks = self._build_review_tasks(brief)
        review_outcomes = self._run_stage(brief, review_tasks, specialist_results)
        (
            review_results,
            review_failures,
            review_tool_contexts,
            review_sandbox_results,
        ) = self._split_outcomes(review_outcomes)

        all_results = [*specialist_results, *review_results]
        all_failures = [*specialist_failures, *review_failures]
        all_tool_contexts = [*specialist_tool_contexts, *review_tool_contexts]
        all_sandbox_results = [
            *specialist_sandbox_results,
            *review_sandbox_results,
        ]
        final_report = self._main_researcher.synthesize(
            brief,
            plan,
            all_results,
            all_failures,
            all_tool_contexts,
            all_sandbox_results,
        )
        return ResearchLabRun(
            brief=brief,
            plan=plan,
            specialist_results=specialist_results,
            review_results=review_results,
            tool_contexts=all_tool_contexts,
            sandbox_results=all_sandbox_results,
            failures=all_failures,
            final_report=final_report,
            execution_order=[
                ResearchRole.MAIN_RESEARCHER,
                *(task.role for task in plan.tasks),
                *REVIEW_ROLES,
                ResearchRole.MAIN_RESEARCHER,
            ],
        )

    def _run_stage(
        self,
        brief: ResearchBrief,
        tasks: Sequence[ResearchTask],
        context_results: Sequence[ResearchAgentResult],
    ) -> list[_TaskOutcome]:
        indexed_outcomes: dict[int, _TaskOutcome] = {}
        worker_count = min(self._max_workers, len(tasks))
        if worker_count == 0:
            return []
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_index: dict[Future[_TaskOutcome], int] = {}
            for index, task in enumerate(tasks):
                future = executor.submit(
                    self._execute_task,
                    brief,
                    task,
                    tuple(context_results),
                )
                future_to_index[future] = index
            for future in as_completed(future_to_index):
                indexed_outcomes[future_to_index[future]] = future.result()
        return [indexed_outcomes[index] for index in range(len(tasks))]

    def _execute_task(
        self,
        brief: ResearchBrief,
        task: ResearchTask,
        context_results: Sequence[ResearchAgentResult],
    ) -> _TaskOutcome:
        worker = self._workers[task.role]
        try:
            self._validate_tool_policy(task, worker)
        except ResearchToolPolicyError as error:
            denied_tools = set(task.requested_tools) - set(worker.spec.allowed_tools)
            tool_context = ResearchToolContext(
                project_id=brief.project_id,
                task_id=task.task_id,
                role=task.role,
                requested_tools=list(task.requested_tools),
                failures=[
                    ResearchToolFailure(
                        capability=capability,
                        code="tool_not_allowed",
                        message=str(error),
                    )
                    for capability in sorted(denied_tools, key=lambda item: item.value)
                ],
            )
            return _TaskOutcome(
                task=task,
                tool_context=tool_context,
                failure=ResearchAgentFailure(
                    project_id=brief.project_id,
                    task_id=task.task_id,
                    role=task.role,
                    error_type=type(error).__name__,
                    message=str(error)[:500],
                ),
            )
        tool_context = self._collect_tools(brief, task)
        try:
            result = worker.execute(
                brief,
                task,
                context_results,
                tool_context,
            )
            tool_context, sandbox_results = self._run_code_sandbox(
                brief,
                task,
                result,
                tool_context,
            )
            return _TaskOutcome(
                task=task,
                tool_context=tool_context,
                result=result,
                sandbox_results=tuple(sandbox_results),
            )
        except Exception as error:
            return _TaskOutcome(
                task=task,
                tool_context=tool_context,
                failure=ResearchAgentFailure(
                    project_id=brief.project_id,
                    task_id=task.task_id,
                    role=task.role,
                    error_type=type(error).__name__,
                    message=f"{type(error).__name__}: research agent execution failed",
                ),
            )

    @staticmethod
    def _split_outcomes(
        outcomes: Sequence[_TaskOutcome],
    ) -> tuple[
        list[ResearchAgentResult],
        list[ResearchAgentFailure],
        list[ResearchToolContext],
        list[CodeSandboxResult],
    ]:
        return (
            [outcome.result for outcome in outcomes if outcome.result is not None],
            [outcome.failure for outcome in outcomes if outcome.failure is not None],
            [outcome.tool_context for outcome in outcomes],
            [result for outcome in outcomes for result in outcome.sandbox_results],
        )

    def _collect_tools(
        self,
        brief: ResearchBrief,
        task: ResearchTask,
    ) -> ResearchToolContext:
        pre_tools = [
            capability
            for capability in task.requested_tools
            if capability is not ToolCapability.CODE_SANDBOX
        ]
        pre_task = task.model_copy(update={"requested_tools": pre_tools})
        if not pre_tools:
            return empty_tool_context(brief, pre_task)
        if self._tool_runtime is None:
            return unavailable_tool_context(brief, pre_task)
        return self._tool_runtime.collect(brief, pre_task)

    def _run_code_sandbox(
        self,
        brief: ResearchBrief,
        task: ResearchTask,
        result: ResearchAgentResult,
        tool_context: ResearchToolContext,
    ) -> tuple[ResearchToolContext, list[CodeSandboxResult]]:
        if ToolCapability.CODE_SANDBOX not in task.requested_tools:
            return (
                tool_context.model_copy(update={"requested_tools": list(task.requested_tools)}),
                [],
            )
        if self._code_sandbox is None:
            return (
                tool_context.model_copy(
                    update={
                        "requested_tools": list(task.requested_tools),
                        "failures": [
                            *tool_context.failures,
                            ResearchToolFailure(
                                capability=ToolCapability.CODE_SANDBOX,
                                code="code_sandbox_unavailable",
                                message="code sandbox executor is not configured",
                            ),
                        ],
                    }
                ),
                [],
            )

        code_artifacts = [
            artifact
            for artifact in result.proposed_artifacts
            if artifact.kind is ArtifactKind.CODE_PATCH
        ]
        if not code_artifacts:
            return (
                tool_context.model_copy(
                    update={
                        "requested_tools": list(task.requested_tools),
                        "failures": [
                            *tool_context.failures,
                            ResearchToolFailure(
                                capability=ToolCapability.CODE_SANDBOX,
                                code="code_artifact_missing",
                                message="developer result did not include a code_patch artifact",
                            ),
                        ],
                    }
                ),
                [],
            )

        sandbox_results = [
            self._code_sandbox.execute(brief, task, artifact) for artifact in code_artifacts
        ]
        sandbox_evidence = [_sandbox_evidence(result) for result in sandbox_results]
        sandbox_failures = [
            ResearchToolFailure(
                capability=ToolCapability.CODE_SANDBOX,
                code=result.failure_code or "sandbox_validation_failed",
                message=result.failure_message or "sandbox validation failed",
            )
            for result in sandbox_results
            if result.status is not CodeSandboxStatus.PASSED
        ]
        return (
            tool_context.model_copy(
                update={
                    "requested_tools": list(task.requested_tools),
                    "evidence": [*tool_context.evidence, *sandbox_evidence],
                    "failures": [*tool_context.failures, *sandbox_failures],
                }
            ),
            sandbox_results,
        )

    @staticmethod
    def _validate_tool_policy(
        task: ResearchTask,
        worker: ResearchLabAgent,
    ) -> None:
        denied_tools = set(task.requested_tools) - set(worker.spec.allowed_tools)
        if denied_tools:
            raise ResearchToolPolicyError(
                f"{task.role.value} cannot use "
                f"{sorted(capability.value for capability in denied_tools)}"
            )

    @staticmethod
    def _build_review_tasks(brief: ResearchBrief) -> list[ResearchTask]:
        return [
            ResearchTask(
                task_id=build_review_task_id(
                    brief.project_id,
                    ResearchRole.EVIDENCE_AUDITOR,
                ),
                role=ResearchRole.EVIDENCE_AUDITOR,
                title="전문 연구 결과 근거 감사",
                instructions=(
                    "전문 연구자 결과의 주장-근거 연결, 출처 신뢰도, 인용 추적성과 빠진 "
                    "반대 근거를 독립적으로 점검한다."
                ),
                expected_output="검증된 주장, 근거 결함, 보완이 필요한 증거 목록",
                requested_tools=[
                    ToolCapability.INTERNAL_CORPUS_SEARCH,
                    ToolCapability.EXTERNAL_SOURCE_SEARCH,
                ],
                search_queries=list(brief.scope[:2]) or [brief.question],
            ),
            ResearchTask(
                task_id=build_review_task_id(
                    brief.project_id,
                    ResearchRole.CRITICAL_REVIEWER,
                ),
                role=ResearchRole.CRITICAL_REVIEWER,
                title="전문 연구 결과 비판 검토",
                instructions=(
                    "전문 연구자 결론을 반증할 수 있는 대안 설명, 정책 부작용, 실행 위험과 "
                    "과도한 일반화를 독립적으로 검토한다."
                ),
                expected_output="핵심 반론, 실패 시나리오, 의사결정 전 확인 항목",
            ),
        ]


def build_review_task_id(project_id: str, role: ResearchRole) -> str:
    """Build a stable bounded task ID for one of the two review roles."""
    if role not in REVIEW_ROLES:
        raise ValueError("review task IDs are available only for review roles")
    digest = sha256(f"{project_id}:{role.value}".encode()).hexdigest()[:20]
    return f"task:{digest}"


def _sandbox_evidence(result: CodeSandboxResult) -> ResearchToolEvidence:
    identity = (
        f"{result.project_id}:{result.task_id}:{result.artifact_id}:"
        f"{result.status.value}:{result.unified_diff}"
    )
    evidence_id = f"sandbox:{sha256(identity.encode()).hexdigest()[:20]}"
    check_summary = "\n".join(
        f"{check.check.value}: {'passed' if check.passed else 'failed'}\n{check.output}"
        for check in result.check_results
    )
    excerpt = (
        f"status={result.status.value}\n"
        f"changed_paths={', '.join(result.changed_paths)}\n"
        f"{check_summary}"
    )[:4_000]
    return ResearchToolEvidence(
        evidence_id=evidence_id,
        capability=ToolCapability.CODE_SANDBOX,
        title=f"Sandbox validation: {result.artifact_id}",
        excerpt=excerpt,
        source_type="sandbox:code_validation",
        locator=result.workspace_lifecycle,
        metadata={
            "artifact_id": result.artifact_id,
            "status": result.status.value,
            "changed_paths": cast(JsonValue, result.changed_paths),
            "applied_to_source": result.applied_to_source,
            "deployed": result.deployed,
        },
    )
