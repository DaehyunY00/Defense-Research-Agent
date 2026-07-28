"""Run the seven-role research lab with deterministic offline model responses."""

import argparse
from collections.abc import Sequence
from pathlib import Path
from tempfile import gettempdir
from typing import cast

from defense_research_agent.agents import (
    FakeModelGateway,
    MainResearcherAgent,
    StructuredResearchAgent,
    build_default_role_specs,
)
from defense_research_agent.domain import (
    ArtifactKind,
    CodeFileChange,
    CodeFileOperation,
    CodeSandboxCheck,
    CodeSandboxValidation,
    DataAnalysisOperation,
    DataAnalysisRequest,
    EvidenceCitation,
    ProposedArtifact,
    PublicationType,
    ResearchAgentResult,
    ResearchBrief,
    ResearchFinding,
    ResearchLabReport,
    ResearchPlan,
    ResearchPublication,
    ResearchRole,
    ResearchTask,
    ToolCapability,
)
from defense_research_agent.issues import MockExternalIssueSearchProvider
from defense_research_agent.repositories import InMemoryResearchPublicationRepository
from defense_research_agent.services import (
    CodeSandboxExecutor,
    DataAnalysisSandboxAdapter,
    ExternalIssueSearchAdapter,
    InternalCorpusSearchAdapter,
    ResearchLabService,
    ResearchToolRuntime,
    build_review_task_id,
    load_default_data_analysis_registry,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute an offline seven-role research-lab demonstration.",
    )
    parser.add_argument("--project-id", default="research-lab-demo")
    parser.add_argument(
        "--question",
        default="공개자료만으로 국방 AI 정책 집행 성과를 어떻게 검증할 수 있는가?",
    )
    parser.add_argument(
        "--objective",
        default="검증 가능한 연구설계와 최소 PoC 범위를 제안한다.",
    )
    parser.add_argument(
        "--issue-fixture",
        type=Path,
        default=Path("tests/fixtures/external_issues.json"),
    )
    return parser


def _plan(project_id: str) -> ResearchPlan:
    tasks = [
        ResearchTask(
            task_id="task:literature",
            role=ResearchRole.LITERATURE_RESEARCHER,
            title="선행연구와 공백 조사",
            instructions="내부 공개 연구자료에서 정책 집행 평가의 선행연구와 공백을 찾는다.",
            expected_output="문서 근거와 연구 공백",
            requested_tools=[ToolCapability.INTERNAL_CORPUS_SEARCH],
            search_queries=["국방 AI"],
        ),
        ResearchTask(
            task_id="task:issues",
            role=ResearchRole.CURRENT_ISSUE_RESEARCHER,
            title="최근 정책 이슈 조사",
            instructions="공식 공개 출처를 우선해 최근 국방 AI 정책 변화를 정리한다.",
            expected_output="날짜와 출처가 있는 이슈 목록",
            requested_tools=[ToolCapability.EXTERNAL_SOURCE_SEARCH],
            search_queries=["국방 AI"],
            policy_domains=["국방인공지능"],
        ),
        ResearchTask(
            task_id="task:methods",
            role=ResearchRole.METHODOLOGY_RESEARCHER,
            title="연구설계 제안",
            instructions="공개자료로 실행 가능한 지표, 비교 기준과 한계를 설계한다.",
            expected_output="측정 가능한 연구설계",
            requested_tools=[ToolCapability.DATA_ANALYSIS_SANDBOX],
            data_analysis_requests=[
                DataAnalysisRequest(
                    request_id="completion-by-program",
                    dataset_id="dataset:policy-outcomes-demo",
                    operation=DataAnalysisOperation.GROUP_MEAN,
                    group_by="program",
                    value_column="completed",
                ),
                DataAnalysisRequest(
                    request_id="planned-completed-correlation",
                    dataset_id="dataset:policy-outcomes-demo",
                    operation=DataAnalysisOperation.PEARSON_CORRELATION,
                    value_column="planned",
                    second_value_column="completed",
                ),
            ],
        ),
        ResearchTask(
            task_id="task:developer",
            role=ResearchRole.DEVELOPER_RESEARCHER,
            title="분석 PoC 설계",
            instructions="격리된 샌드박스에서 검증할 최소 분석 PoC와 테스트를 제안한다.",
            expected_output="코드 변경안과 검증 명령",
            requested_tools=[
                ToolCapability.INTERNAL_CORPUS_SEARCH,
                ToolCapability.CODE_SANDBOX,
            ],
            search_queries=["성과 지표"],
        ),
    ]
    return ResearchPlan(
        project_id=project_id,
        rationale="문헌, 최신 이슈, 방법론과 구현 가능성을 독립적으로 조사한다.",
        tasks=tasks,
        success_criteria=[
            "모든 핵심 주장에 추적 가능한 근거 또는 명시적 근거 공백이 있다.",
            "PoC 범위와 인간 승인 지점이 분명하다.",
        ],
    )


def _result(
    project_id: str,
    task_id: str,
    role: ResearchRole,
    statement: str,
    *,
    artifact: ProposedArtifact | None = None,
    evidence: list[EvidenceCitation] | None = None,
) -> ResearchAgentResult:
    return ResearchAgentResult(
        project_id=project_id,
        task_id=task_id,
        role=role,
        summary=statement,
        findings=[
            ResearchFinding(
                finding_id=f"finding:{role.value}",
                statement=statement,
                evidence=evidence or [],
                confidence=0.7,
                caveats=["실제 자료 연결 전의 결정적 오프라인 예시다."],
            )
        ],
        evidence_gaps=["실제 실행에서는 문서·페이지와 공식 URL 근거를 연결해야 한다."],
        recommendations=["다음 단계에서 실제 검색 도구 어댑터를 주입한다."],
        proposed_artifacts=[artifact] if artifact is not None else [],
    )


def _report(project_id: str, source_task_ids: list[str]) -> ResearchLabReport:
    return ResearchLabReport(
        project_id=project_id,
        executive_summary=(
            "7개 역할의 계획·병렬 조사·독립 검토·종합 경로가 구조화 계약으로 실행됐다. "
            "현재 결과는 실제 사실판단이 아니라 오케스트레이션 검증용이다."
        ),
        disagreements=["실제 출처 연결 전에는 정책 효과의 방향을 확정할 수 없다."],
        evidence_gaps=["내부 문서 페이지와 최신 공식 출처를 실제 도구로 연결해야 한다."],
        poc_recommendations=[
            "실제 공개 데이터셋을 검토 후 레지스트리에 등록하고 같은 분석 계약으로 교체한다."
        ],
        next_steps=[
            "Claude 구조화 ModelGateway 연결",
            "GCP 주 애플리케이션 Cloud Run API 구성",
            "Secret Manager에서 Claude API 키 주입",
        ],
        source_task_ids=source_task_ids,
    )


def _build_service(project_id: str, issue_fixture: Path) -> ResearchLabService:
    specs = {spec.role: spec for spec in build_default_role_specs()}
    plan = _plan(project_id)
    audit_task_id = build_review_task_id(project_id, ResearchRole.EVIDENCE_AUDITOR)
    critical_task_id = build_review_task_id(project_id, ResearchRole.CRITICAL_REVIEWER)
    source_task_ids = [
        *(task.task_id for task in plan.tasks),
        audit_task_id,
        critical_task_id,
    ]
    main_gateway = FakeModelGateway([plan, _report(project_id, source_task_ids)])
    data_analysis_registry = load_default_data_analysis_registry()
    main_researcher = MainResearcherAgent(
        specs[ResearchRole.MAIN_RESEARCHER],
        main_gateway,
        data_analysis_catalog=data_analysis_registry.catalog(),
    )
    developer_artifact = ProposedArtifact(
        artifact_id="artifact:analysis-poc",
        kind=ArtifactKind.CODE_PATCH,
        title="공개자료 분석 PoC",
        summary="검색 결과를 지표 테이블로 변환하는 최소 코드와 단위 테스트를 제안한다.",
        repository_path="src/defense_research_agent/poc/policy_metrics.py",
        validation_commands=["uv run pytest tests/unit/poc/test_policy_metrics.py"],
        code_changes=[
            CodeFileChange(
                relative_path="src/defense_research_agent/poc/__init__.py",
                operation=CodeFileOperation.CREATE,
                content='"""Research-lab PoC modules."""\n',
            ),
            CodeFileChange(
                relative_path="src/defense_research_agent/poc/policy_metrics.py",
                operation=CodeFileOperation.CREATE,
                content=(
                    '"""Deterministic public policy metric helpers."""\n\n'
                    "def completion_rate(completed: int, planned: int) -> float:\n"
                    '    """Return a bounded completion rate for public aggregate counts."""\n'
                    "    if completed < 0 or planned <= 0:\n"
                    '        raise ValueError("counts must be non-negative and planned positive")\n'
                    "    return min(completed / planned, 1.0)\n"
                ),
            ),
            CodeFileChange(
                relative_path="tests/unit/poc/test_policy_metrics.py",
                operation=CodeFileOperation.CREATE,
                content=(
                    '"""Tests for the policy metrics PoC."""\n\n'
                    "from defense_research_agent.poc.policy_metrics import completion_rate\n\n\n"
                    "def test_completion_rate_is_bounded() -> None:\n"
                    "    assert completion_rate(8, 10) == 0.8\n"
                    "    assert completion_rate(12, 10) == 1.0\n"
                ),
            ),
        ],
        sandbox_validations=[
            CodeSandboxValidation(
                check=CodeSandboxCheck.PYTHON_COMPILE,
                targets=[
                    "src/defense_research_agent/poc/__init__.py",
                    "src/defense_research_agent/poc/policy_metrics.py",
                    "tests/unit/poc/test_policy_metrics.py",
                ],
            )
        ],
    )
    internal_policy_evidence = EvidenceCitation(
        evidence_id="pub:ai-policy",
        title="국방 AI 정책 집행 연구",
        source_type="publication:kida_brief",
        locator="data/Brief/ai-policy.pdf",
    )
    internal_metrics_evidence = EvidenceCitation(
        evidence_id="pub:metrics",
        title="국방사업 성과지표 설계",
        source_type="publication:defense_policy_research",
        locator="data/국방정책연구/metrics.pdf",
    )
    external_policy_evidence = EvidenceCitation(
        evidence_id="ext:gov:ai-workforce-policy",
        title="국방 AI 인력정책 추진계획",
        source_type="external:government_policy",
        source_url="https://www.mnd.go.kr/policy/ai-workforce",
        untrusted_external_content=True,
    )
    completion_analysis_evidence = EvidenceCitation(
        evidence_id="analysis:completion-by-program",
        title="공개 정책성과 지표 예제 데이터: group_mean",
        source_type="sandbox:data_analysis",
        locator="package:default_data_analysis_datasets.json",
    )
    correlation_analysis_evidence = EvidenceCitation(
        evidence_id="analysis:planned-completed-correlation",
        title="공개 정책성과 지표 예제 데이터: pearson_correlation",
        source_type="sandbox:data_analysis",
        locator="package:default_data_analysis_datasets.json",
    )
    responses = {
        ResearchRole.LITERATURE_RESEARCHER: _result(
            project_id,
            "task:literature",
            ResearchRole.LITERATURE_RESEARCHER,
            "선행연구와 최신 정책 문서를 연결하는 비교 틀이 필요하다.",
            evidence=[internal_policy_evidence],
        ),
        ResearchRole.CURRENT_ISSUE_RESEARCHER: _result(
            project_id,
            "task:issues",
            ResearchRole.CURRENT_ISSUE_RESEARCHER,
            "최근 이슈는 공식 발표일과 실제 시행일을 구분해야 한다.",
            evidence=[external_policy_evidence],
        ),
        ResearchRole.METHODOLOGY_RESEARCHER: _result(
            project_id,
            "task:methods",
            ResearchRole.METHODOLOGY_RESEARCHER,
            "프로그램별 완료량과 계획·완료 상관을 기초 지표로 삼되 인과효과로 해석하면 안 된다.",
            evidence=[completion_analysis_evidence, correlation_analysis_evidence],
        ),
        ResearchRole.DEVELOPER_RESEARCHER: _result(
            project_id,
            "task:developer",
            ResearchRole.DEVELOPER_RESEARCHER,
            "결정적 fixture와 지표 계산 함수를 먼저 만드는 PoC가 가능하다.",
            artifact=developer_artifact,
            evidence=[internal_metrics_evidence],
        ),
        ResearchRole.EVIDENCE_AUDITOR: _result(
            project_id,
            audit_task_id,
            ResearchRole.EVIDENCE_AUDITOR,
            "현재 데모의 주장은 실제 출처가 연결되지 않아 검증 완료로 볼 수 없다.",
            evidence=[internal_policy_evidence, external_policy_evidence],
        ),
        ResearchRole.CRITICAL_REVIEWER: _result(
            project_id,
            critical_task_id,
            ResearchRole.CRITICAL_REVIEWER,
            "측정 가능한 변화가 정책의 인과 효과를 곧바로 뜻하지 않는다는 반론이 필요하다.",
        ),
    }
    workers = {
        role: StructuredResearchAgent(specs[role], FakeModelGateway([response]))
        for role, response in responses.items()
    }
    repository = InMemoryResearchPublicationRepository(
        [
            ResearchPublication(
                publication_id="pub:ai-policy",
                publication_type=PublicationType.KIDA_BRIEF,
                title="국방 AI 정책 집행 연구",
                abstract="국방 AI 정책 집행의 제도와 인력정책을 분석한다.",
                local_path="data/Brief/ai-policy.pdf",
            ),
            ResearchPublication(
                publication_id="pub:metrics",
                publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
                title="국방사업 성과지표 설계",
                abstract="공개자료를 이용한 정책 성과 지표와 검증 방법을 제안한다.",
                local_path="data/국방정책연구/metrics.pdf",
            ),
        ]
    )
    tool_runtime = ResearchToolRuntime(
        [
            InternalCorpusSearchAdapter(repository),
            ExternalIssueSearchAdapter(
                MockExternalIssueSearchProvider(issue_fixture),
            ),
            DataAnalysisSandboxAdapter(data_analysis_registry),
        ]
    )
    project_root = Path(__file__).resolve().parents[3]
    code_sandbox = CodeSandboxExecutor(
        source_root=project_root,
        sandbox_root=Path(gettempdir()) / "defense-research-agent-sandboxes",
    )
    return ResearchLabService(
        main_researcher,
        workers,
        tool_runtime=tool_runtime,
        code_sandbox=code_sandbox,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline lab and write one JSON result to standard output."""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    project_id = cast(str, args.project_id)
    brief = ResearchBrief(
        project_id=project_id,
        question=cast(str, args.question),
        objective=cast(str, args.objective),
        scope=["국방 AI"],
        constraints=["비공개·비밀 자료 제외", "자동 승인 금지"],
        deliverables=["연구설계", "분석 PoC 제안"],
    )
    issue_fixture = cast(Path, args.issue_fixture)
    run = _build_service(project_id, issue_fixture).run(brief)
    print(run.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
