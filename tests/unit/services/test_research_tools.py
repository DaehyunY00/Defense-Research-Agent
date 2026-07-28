"""Tests for allow-listed internal and external research tool adapters."""

from datetime import date
from pathlib import Path

from defense_research_agent.domain import (
    PublicationType,
    ResearchBrief,
    ResearchPublication,
    ResearchRole,
    ResearchTask,
    ResearchToolOutput,
    ToolCapability,
)
from defense_research_agent.issues import MockExternalIssueSearchProvider
from defense_research_agent.repositories import InMemoryResearchPublicationRepository
from defense_research_agent.services import (
    ExternalIssueSearchAdapter,
    InternalCorpusSearchAdapter,
    ResearchToolAdapter,
    ResearchToolRuntime,
)

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "external_issues.json"


def _brief() -> ResearchBrief:
    return ResearchBrief(
        project_id="project:tools",
        question="국방 AI 정책 성과를 어떻게 검증하는가?",
        objective="공개 근거를 수집한다.",
        scope=["국방 AI"],
        deliverables=["근거 묶음"],
        evidence_start_date=date(2026, 6, 1),
        evidence_end_date=date(2026, 7, 31),
    )


def _task(
    role: ResearchRole,
    capability: ToolCapability,
    query: str,
) -> ResearchTask:
    return ResearchTask(
        task_id=f"task:{role.value}",
        role=role,
        title="도구 검색",
        instructions="허용된 도구로 공개 근거를 검색한다.",
        expected_output="추적 가능한 근거",
        requested_tools=[capability],
        search_queries=[query],
        policy_domains=["국방인공지능"],
    )


def test_internal_adapter_returns_repository_evidence_with_match_metadata() -> None:
    repository = InMemoryResearchPublicationRepository(
        [
            ResearchPublication(
                publication_id="pub:ai",
                publication_type=PublicationType.KIDA_BRIEF,
                title="국방 AI 정책 분석",
                abstract="국방 인공지능 정책의 집행과 성과를 분석한다.",
                local_path="data/Brief/ai.pdf",
            ),
            ResearchPublication(
                publication_id="pub:other",
                publication_type=PublicationType.RESEARCH_REPORT,
                title="병력구조 연구",
            ),
        ]
    )
    adapter = InternalCorpusSearchAdapter(repository)

    output = adapter.execute(
        _brief(),
        _task(
            ResearchRole.LITERATURE_RESEARCHER,
            ToolCapability.INTERNAL_CORPUS_SEARCH,
            "국방 AI",
        ),
    )

    assert [item.evidence_id for item in output.evidence] == ["pub:ai"]
    assert output.evidence[0].locator == "data/Brief/ai.pdf"
    assert output.evidence[0].metadata["matched_terms"] == ["국방", "ai"]
    assert output.failures == []


def test_external_adapter_normalizes_untrusted_sources_and_keeps_partial_errors() -> None:
    adapter = ExternalIssueSearchAdapter(
        MockExternalIssueSearchProvider(FIXTURE_PATH),
        limit_per_query=10,
    )

    output = adapter.execute(
        _brief(),
        _task(
            ResearchRole.CURRENT_ISSUE_RESEARCHER,
            ToolCapability.EXTERNAL_SOURCE_SEARCH,
            "국방 AI",
        ),
    )

    evidence_by_id = {item.evidence_id: item for item in output.evidence}
    official = evidence_by_id["ext:gov:ai-workforce-policy"]
    assert official.untrusted_external_content is True
    assert official.source_url == "https://www.mnd.go.kr/policy/ai-workforce"
    assert official.metadata["reliability_tier"] == "tier_1_official"
    assert any(failure.code == "source_validation_error" for failure in output.failures)


def test_runtime_records_an_unconfigured_requested_capability() -> None:
    runtime = ResearchToolRuntime([])
    task = _task(
        ResearchRole.CURRENT_ISSUE_RESEARCHER,
        ToolCapability.EXTERNAL_SOURCE_SEARCH,
        "국방 AI",
    )

    context = runtime.collect(_brief(), task)

    assert context.evidence == []
    assert context.failures[0].code == "adapter_unavailable"
    assert context.requested_tools == [ToolCapability.EXTERNAL_SOURCE_SEARCH]


class _SecretLeakingAdapter(ResearchToolAdapter):
    capability = ToolCapability.EXTERNAL_SOURCE_SEARCH

    def execute(self, brief: ResearchBrief, task: ResearchTask) -> ResearchToolOutput:
        del brief, task
        raise RuntimeError("provider failed with ANTHROPIC_API_KEY=sk-ant-must-not-leak")


def test_runtime_sanitizes_unexpected_adapter_exceptions() -> None:
    runtime = ResearchToolRuntime([_SecretLeakingAdapter()])
    task = _task(
        ResearchRole.CURRENT_ISSUE_RESEARCHER,
        ToolCapability.EXTERNAL_SOURCE_SEARCH,
        "국방 AI",
    )

    context = runtime.collect(_brief(), task)

    serialized = context.model_dump_json()
    assert context.failures[0].code == "adapter_failure"
    assert context.failures[0].message == "RuntimeError: adapter execution failed"
    assert "sk-ant" not in serialized
    assert "ANTHROPIC_API_KEY" not in serialized
