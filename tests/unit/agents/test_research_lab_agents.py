"""Tests for role policy and structured research-agent boundaries."""

import pytest

from defense_research_agent.agents import (
    FakeModelGateway,
    ResearchAgentOutputValidationError,
    StructuredResearchAgent,
    build_default_role_specs,
)
from defense_research_agent.domain import (
    EvidenceCitation,
    ResearchAgentResult,
    ResearchBrief,
    ResearchFinding,
    ResearchRole,
    ResearchTask,
    ResearchToolContext,
    ResearchToolEvidence,
    ToolCapability,
)


def _brief() -> ResearchBrief:
    return ResearchBrief(
        project_id="project:agent",
        question="공개자료로 정책 성과를 어떻게 검증하는가?",
        objective="재현 가능한 검증 계획을 만든다.",
        deliverables=["연구 결과"],
    )


def _task() -> ResearchTask:
    return ResearchTask(
        task_id="task:developer",
        role=ResearchRole.DEVELOPER_RESEARCHER,
        title="분석 PoC",
        instructions="최소 분석 PoC와 테스트 계획을 만든다.",
        expected_output="검증 가능한 코드 변경안",
    )


def _result(
    *,
    task_id: str = "task:developer",
    evidence_id: str | None = None,
) -> ResearchAgentResult:
    return ResearchAgentResult(
        project_id="project:agent",
        task_id=task_id,
        role=ResearchRole.DEVELOPER_RESEARCHER,
        summary="격리된 코드 샌드박스에서 결정적 fixture를 먼저 검증한다.",
        findings=[
            ResearchFinding(
                finding_id="finding:poc",
                statement="PoC는 외부 API 없이 시작할 수 있다.",
                evidence=(
                    [
                        EvidenceCitation(
                            evidence_id=evidence_id,
                            title="PoC 근거",
                            source_type="publication:kida_brief",
                        )
                    ]
                    if evidence_id is not None
                    else []
                ),
                confidence=0.8,
            )
        ],
    )


def _tool_context(*, evidence_id: str | None = None) -> ResearchToolContext:
    return ResearchToolContext(
        project_id="project:agent",
        task_id="task:developer",
        role=ResearchRole.DEVELOPER_RESEARCHER,
        requested_tools=(
            [ToolCapability.INTERNAL_CORPUS_SEARCH] if evidence_id is not None else []
        ),
        evidence=(
            [
                ResearchToolEvidence(
                    evidence_id=evidence_id,
                    capability=ToolCapability.INTERNAL_CORPUS_SEARCH,
                    title="PoC 근거",
                    source_type="publication:kida_brief",
                )
            ]
            if evidence_id is not None
            else []
        ),
    )


def test_default_catalog_has_seven_roles_and_scopes_developer_tools() -> None:
    specs = build_default_role_specs()
    by_role = {spec.role: spec for spec in specs}

    assert len(specs) == 7
    assert set(by_role) == set(ResearchRole)
    assert ToolCapability.CODE_SANDBOX in by_role[ResearchRole.DEVELOPER_RESEARCHER].allowed_tools
    assert ToolCapability.CODE_SANDBOX not in by_role[ResearchRole.MAIN_RESEARCHER].allowed_tools
    assert by_role[ResearchRole.CRITICAL_REVIEWER].allowed_tools == [ToolCapability.ARTIFACT_READ]


def test_structured_agent_records_route_and_allow_list_in_model_call() -> None:
    spec = {item.role: item for item in build_default_role_specs()}[
        ResearchRole.DEVELOPER_RESEARCHER
    ]
    gateway = FakeModelGateway([_result()])
    agent = StructuredResearchAgent(spec, gateway)

    output = agent.execute(_brief(), _task(), (), _tool_context())

    assert output.task_id == "task:developer"
    assert gateway.calls[0].task_type == "research_lab.execute.developer_researcher"
    assert gateway.calls[0].metadata["model_provider"] == "fake"
    assert gateway.calls[0].metadata["allowed_tools"] == [
        "artifact_read",
        "internal_corpus_search",
        "code_sandbox",
    ]


def test_structured_agent_rejects_schema_valid_output_for_another_task() -> None:
    spec = {item.role: item for item in build_default_role_specs()}[
        ResearchRole.DEVELOPER_RESEARCHER
    ]
    agent = StructuredResearchAgent(
        spec,
        FakeModelGateway([_result(task_id="task:other")]),
    )

    with pytest.raises(ResearchAgentOutputValidationError, match="does not match"):
        agent.execute(_brief(), _task(), (), _tool_context())


def test_structured_agent_accepts_only_evidence_supplied_by_tool_context() -> None:
    spec = {item.role: item for item in build_default_role_specs()}[
        ResearchRole.DEVELOPER_RESEARCHER
    ]
    accepted = StructuredResearchAgent(
        spec,
        FakeModelGateway([_result(evidence_id="pub:known")]),
    )
    rejected = StructuredResearchAgent(
        spec,
        FakeModelGateway([_result(evidence_id="pub:invented")]),
    )

    output = accepted.execute(
        _brief(),
        _task(),
        (),
        _tool_context(evidence_id="pub:known"),
    )

    assert output.findings[0].evidence[0].evidence_id == "pub:known"
    with pytest.raises(ResearchAgentOutputValidationError, match="unknown evidence"):
        rejected.execute(
            _brief(),
            _task(),
            (),
            _tool_context(evidence_id="pub:known"),
        )
