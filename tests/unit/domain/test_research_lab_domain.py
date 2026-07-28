"""Tests for research-lab domain invariants."""

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import (
    CodeFileChange,
    CodeFileOperation,
    DataAnalysisOperation,
    DataAnalysisRequest,
    ResearchPlan,
    ResearchRole,
    ResearchTask,
    ToolCapability,
)


def _task(task_id: str, role: ResearchRole) -> ResearchTask:
    return ResearchTask(
        task_id=task_id,
        role=role,
        title="독립 연구 과업",
        instructions="공개자료를 사용해 배정된 관점만 조사한다.",
        expected_output="근거와 한계가 있는 구조화 결과",
    )


def test_research_plan_accepts_unique_specialist_assignments() -> None:
    plan = ResearchPlan(
        project_id="project:lab",
        rationale="서로 다른 전문 관점을 병렬 조사한다.",
        tasks=[
            _task("task:literature", ResearchRole.LITERATURE_RESEARCHER),
            _task("task:developer", ResearchRole.DEVELOPER_RESEARCHER),
        ],
        success_criteria=["모든 주장에 근거 또는 근거 공백이 있다."],
    )

    assert [task.role for task in plan.tasks] == [
        ResearchRole.LITERATURE_RESEARCHER,
        ResearchRole.DEVELOPER_RESEARCHER,
    ]


@pytest.mark.parametrize(
    "tasks, message",
    [
        (
            [
                _task("task:same", ResearchRole.LITERATURE_RESEARCHER),
                _task("task:same", ResearchRole.METHODOLOGY_RESEARCHER),
            ],
            "task_id",
        ),
        (
            [
                _task("task:one", ResearchRole.LITERATURE_RESEARCHER),
                _task("task:two", ResearchRole.LITERATURE_RESEARCHER),
            ],
            "at most one",
        ),
        (
            [_task("task:pi", ResearchRole.MAIN_RESEARCHER)],
            "specialist roles",
        ),
    ],
)
def test_research_plan_rejects_ambiguous_or_privileged_tasks(
    tasks: list[ResearchTask],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ResearchPlan(
            project_id="project:lab",
            rationale="잘못된 계획",
            tasks=tasks,
            success_criteria=["검증"],
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        "../outside.py",
        "/absolute.py",
        "data/metadata.py",
        "src\\windows.py",
    ],
)
def test_code_change_rejects_unsafe_or_protected_paths(relative_path: str) -> None:
    with pytest.raises(ValidationError):
        CodeFileChange(
            relative_path=relative_path,
            operation=CodeFileOperation.CREATE,
            content="VALUE = 1\n",
        )


def test_replace_code_change_requires_expected_checksum() -> None:
    with pytest.raises(ValidationError, match="expected_sha256"):
        CodeFileChange(
            relative_path="src/defense_research_agent/poc/example.py",
            operation=CodeFileOperation.REPLACE,
            content="VALUE = 2\n",
        )


def test_data_analysis_request_rejects_operation_specific_extra_columns() -> None:
    with pytest.raises(ValidationError, match="unsupported column"):
        DataAnalysisRequest(
            request_id="request:count",
            dataset_id="dataset:public",
            operation=DataAnalysisOperation.ROW_COUNT,
            value_column="completed",
        )


def test_data_analysis_sandbox_requires_methodology_request() -> None:
    request = DataAnalysisRequest(
        request_id="request:count",
        dataset_id="dataset:public",
        operation=DataAnalysisOperation.ROW_COUNT,
    )

    with pytest.raises(ValidationError, match="methodology role"):
        ResearchTask(
            task_id="task:developer",
            role=ResearchRole.DEVELOPER_RESEARCHER,
            title="Unsafe analysis assignment",
            instructions="Attempt to run analysis from the wrong role.",
            expected_output="Rejected task",
            requested_tools=[ToolCapability.DATA_ANALYSIS_SANDBOX],
            data_analysis_requests=[request],
        )


def test_data_analysis_sandbox_rejects_arbitrary_sql_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        DataAnalysisRequest.model_validate(
            {
                "request_id": "request:sql",
                "dataset_id": "dataset:public",
                "operation": "row_count",
                "sql": "DROP TABLE anything",
            }
        )
