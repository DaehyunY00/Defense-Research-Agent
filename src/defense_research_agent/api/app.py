"""Private Cloud Run API for asynchronous research projects."""

from typing import cast

from fastapi import FastAPI, HTTPException, Request, status

from defense_research_agent import __version__
from defense_research_agent.domain import ResearchLabRun
from defense_research_agent.domain.research_project import (
    CreateResearchProject,
    ResearchLabReviewSubmission,
    ResearchProjectRecord,
)
from defense_research_agent.repositories.research_projects import (
    ResearchProjectNotFoundError,
    ResearchProjectStateConflictError,
)
from defense_research_agent.services.gcp_research_runtime import (
    build_gcp_research_application,
)
from defense_research_agent.services.research_projects import (
    ResearchJobDispatchError,
    ResearchProjectApplicationService,
    ResearchResultIntegrityError,
    ResearchResultNotReadyError,
)


def create_app(
    service: ResearchProjectApplicationService | None = None,
) -> FastAPI:
    """Create an injectable API while deferring Google client construction."""
    application = FastAPI(
        title="Defense Research Agent API",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )
    application.state.research_service = service

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.post(
        "/v1/research-projects",
        response_model=ResearchProjectRecord,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_project(
        payload: CreateResearchProject,
        request: Request,
    ) -> ResearchProjectRecord:
        try:
            return _service(request).create(payload)
        except ResearchJobDispatchError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="research_job_dispatch_failed",
            ) from error

    @application.get(
        "/v1/research-projects/{project_id}",
        response_model=ResearchProjectRecord,
    )
    def get_project(project_id: str, request: Request) -> ResearchProjectRecord:
        try:
            return _service(request).get(project_id)
        except ResearchProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="research_project_not_found",
            ) from error

    @application.get(
        "/v1/research-projects/{project_id}/result",
        response_model=ResearchLabRun,
    )
    def get_result(project_id: str, request: Request) -> ResearchLabRun:
        try:
            return _service(request).get_result(project_id)
        except ResearchProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="research_project_not_found",
            ) from error
        except ResearchResultNotReadyError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="research_result_not_ready",
            ) from error
        except ResearchResultIntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="research_result_integrity_error",
            ) from error

    @application.post(
        "/v1/research-projects/{project_id}/review",
        response_model=ResearchProjectRecord,
    )
    def review_project(
        project_id: str,
        payload: ResearchLabReviewSubmission,
        request: Request,
    ) -> ResearchProjectRecord:
        try:
            return _service(request).review(project_id, payload)
        except ResearchProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="research_project_not_found",
            ) from error
        except ResearchProjectStateConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="research_project_not_awaiting_review",
            ) from error

    return application


def _service(request: Request) -> ResearchProjectApplicationService:
    configured = getattr(request.app.state, "research_service", None)
    if configured is None:
        configured = build_gcp_research_application()
        request.app.state.research_service = configured
    return cast(ResearchProjectApplicationService, configured)


app = create_app()
