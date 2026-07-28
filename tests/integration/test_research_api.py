"""HTTP contract tests for the private Cloud Run research API."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
from fastapi import FastAPI

from defense_research_agent.api import create_app
from defense_research_agent.domain import (
    ResearchLabReport,
    ResearchLabRun,
    ResearchPlan,
    ResearchRole,
    ResearchTask,
)
from defense_research_agent.repositories.research_projects import (
    InMemoryResearchProjectRepository,
)
from defense_research_agent.services.research_lab import ResearchLabService
from defense_research_agent.services.research_projects import (
    InMemoryResearchRunStore,
    ResearchJobDispatcher,
    ResearchProjectApplicationService,
    ResearchProjectRunner,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


class ApiDispatcher(ResearchJobDispatcher):
    def dispatch(self, project_id: str) -> str:
        return f"operations/{project_id}"


class ApiLab:
    def run(self, brief: object) -> ResearchLabRun:
        from defense_research_agent.domain import ResearchBrief

        validated = ResearchBrief.model_validate(brief)
        task = ResearchTask(
            task_id="task:literature",
            role=ResearchRole.LITERATURE_RESEARCHER,
            title="문헌 검토",
            instructions="문헌을 검토한다.",
            expected_output="문헌 결과",
        )
        return ResearchLabRun(
            brief=validated,
            plan=ResearchPlan(
                project_id=validated.project_id,
                rationale="문헌 과업을 배정한다.",
                tasks=[task],
                success_criteria=["근거를 확인한다."],
            ),
            final_report=ResearchLabReport(
                project_id=validated.project_id,
                executive_summary="연구 결과",
            ),
        )


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    json: dict[str, object] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, json=json)

    return asyncio.run(send())


def test_api_accepts_polls_returns_result_and_records_human_review() -> None:
    times = iter(NOW + timedelta(seconds=index) for index in range(20))
    repository = InMemoryResearchProjectRepository()
    store = InMemoryResearchRunStore()
    service = ResearchProjectApplicationService(
        repository,
        ApiDispatcher(),
        store,
        clock=lambda: next(times),
        id_factory=lambda: "research-api",
    )
    app = create_app(service)

    created = _request(
        app,
        "POST",
        "/v1/research-projects",
        json={
            "question": "국방정책 효과를 어떻게 검증할 것인가?",
            "objective": "공개자료 기반 연구설계를 만든다.",
            "deliverables": ["연구보고서"],
        },
    )

    assert created.status_code == 202
    assert created.json()["status"] == "queued"
    pending_result = _request(
        app,
        "GET",
        "/v1/research-projects/research-api/result",
    )
    assert pending_result.status_code == 409

    runner = ResearchProjectRunner(
        repository,
        store,
        lambda: cast(ResearchLabService, ApiLab()),
        clock=lambda: next(times),
    )
    runner.run("research-api")

    result = _request(app, "GET", "/v1/research-projects/research-api/result")
    assert result.status_code == 200
    assert result.json()["status"] == "awaiting_human_review"

    reviewed = _request(
        app,
        "POST",
        "/v1/research-projects/research-api/review",
        json={
            "decision": "approve",
            "reviewer": "human-reviewer",
            "comment": "검토 완료",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "approved"
    assert reviewed.json()["human_approval_required"] is True


def test_api_rejects_unknown_fields_and_missing_project() -> None:
    service = ResearchProjectApplicationService(
        InMemoryResearchProjectRepository(),
        ApiDispatcher(),
        InMemoryResearchRunStore(),
    )
    app = create_app(service)

    invalid = _request(
        app,
        "POST",
        "/v1/research-projects",
        json={
            "question": "질문",
            "objective": "목적",
            "claude_api_key": "must-never-be-accepted",
        },
    )
    assert invalid.status_code == 422

    missing = _request(app, "GET", "/v1/research-projects/not-found")
    assert missing.status_code == 404
