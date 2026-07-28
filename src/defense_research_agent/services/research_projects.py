"""Application services for asynchronous research projects and human review."""

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from defense_research_agent.domain import ResearchLabRun
from defense_research_agent.domain.research_project import (
    CreateResearchProject,
    ResearchLabReviewDecision,
    ResearchLabReviewEvent,
    ResearchLabReviewSubmission,
    ResearchProjectRecord,
    ResearchProjectStatus,
    utc_now,
)
from defense_research_agent.repositories.research_projects import (
    ResearchProjectNotFoundError,
    ResearchProjectRepository,
    ResearchProjectStateConflictError,
)
from defense_research_agent.services.research_lab import ResearchLabService

_MAX_RESEARCH_RUN_BYTES = 20 * 1024 * 1024


class ResearchJobDispatchError(RuntimeError):
    """Raised when an accepted project cannot be submitted to its worker."""


class ResearchResultNotReadyError(ValueError):
    """Raised when result retrieval occurs before a bound artifact exists."""


class ResearchResultIntegrityError(ValueError):
    """Raised when stored result bytes do not match their project binding."""


class ResearchJobDispatcher(ABC):
    """Submit one project to an already-deployed asynchronous worker."""

    @abstractmethod
    def dispatch(self, project_id: str) -> str:
        """Return a provider operation identifier without waiting for completion."""


class ResearchRunStore(ABC):
    """Immutable object-store boundary for complete research-lab runs."""

    @abstractmethod
    def put(self, project_id: str, payload: bytes) -> str:
        """Create one result object and return its stable object name."""

    @abstractmethod
    def get(self, object_name: str) -> bytes:
        """Read one exact result object without listing storage."""


class InMemoryResearchRunStore(ResearchRunStore):
    """Create-only object store for tests and local composition."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, project_id: str, payload: bytes) -> str:
        object_name = research_result_object_name(project_id)
        if object_name in self.objects:
            raise ValueError("research result object already exists")
        self.objects[object_name] = bytes(payload)
        return object_name

    def get(self, object_name: str) -> bytes:
        try:
            return self.objects[object_name]
        except KeyError as error:
            raise ResearchProjectNotFoundError(object_name) from error


class ResearchProjectApplicationService:
    """Accept projects, expose results, and preserve the human-review gate."""

    def __init__(
        self,
        repository: ResearchProjectRepository,
        dispatcher: ResearchJobDispatcher,
        run_store: ResearchRunStore,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher
        self._run_store = run_store
        self._clock = clock
        self._id_factory = id_factory or (lambda: f"research-{uuid4().hex}")

    def create(self, request: CreateResearchProject) -> ResearchProjectRecord:
        """Persist a request before dispatch and return the durable queue record."""
        project_id = self._id_factory()
        now = self._clock()
        record = ResearchProjectRecord(
            project_id=project_id,
            brief=request.to_brief(project_id),
            status=ResearchProjectStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self._repository.create(record)
        try:
            execution_name = self._dispatcher.dispatch(project_id)
        except Exception as error:
            self._repository.fail(
                project_id,
                failure_code="job_dispatch_failed",
                failure_message=f"Research job dispatch failed ({type(error).__name__})",
                updated_at=self._clock(),
            )
            raise ResearchJobDispatchError("research job dispatch failed") from error
        return self._repository.record_dispatch(
            project_id,
            execution_name,
            self._clock(),
        )

    def get(self, project_id: str) -> ResearchProjectRecord:
        """Return one project or raise a stable not-found error."""
        record = self._repository.get(project_id)
        if record is None:
            raise ResearchProjectNotFoundError(project_id)
        return record

    def get_result(self, project_id: str) -> ResearchLabRun:
        """Download, integrity-check, and schema-validate one completed run."""
        record = self.get(project_id)
        if (
            record.result_object is None
            or record.result_sha256 is None
            or record.result_size_bytes is None
        ):
            raise ResearchResultNotReadyError("research result is not ready")
        payload = self._run_store.get(record.result_object)
        if len(payload) != record.result_size_bytes:
            raise ResearchResultIntegrityError("research result size mismatch")
        if sha256(payload).hexdigest() != record.result_sha256:
            raise ResearchResultIntegrityError("research result checksum mismatch")
        try:
            run = ResearchLabRun.model_validate_json(payload)
        except ValueError as error:
            raise ResearchResultIntegrityError("research result schema is invalid") from error
        if run.brief.project_id != project_id or run.final_report.project_id != project_id:
            raise ResearchResultIntegrityError("research result project binding mismatch")
        return run

    def review(
        self,
        project_id: str,
        submission: ResearchLabReviewSubmission,
    ) -> ResearchProjectRecord:
        """Append one human decision without changing or deploying the report."""
        current = self.get(project_id)
        if current.status not in {
            ResearchProjectStatus.AWAITING_HUMAN_REVIEW,
            ResearchProjectStatus.HELD,
        }:
            raise ResearchProjectStateConflictError(
                "project must be awaiting_human_review or held before review"
            )
        reviewed_at = max(current.updated_at, self._clock())
        sequence = len(current.review_history) + 1
        identity = "\0".join(
            (
                project_id,
                submission.decision.value,
                submission.reviewer,
                str(sequence),
                reviewed_at.isoformat(),
            )
        )
        event = ResearchLabReviewEvent(
            event_id=f"review:{sha256(identity.encode()).hexdigest()[:24]}",
            decision=submission.decision,
            reviewer=submission.reviewer,
            comment=submission.comment,
            requested_edits=submission.requested_edits,
            reviewed_at=reviewed_at,
            sequence=sequence,
        )
        return self._repository.append_review(
            project_id,
            event,
            _reviewed_status(submission.decision),
            reviewed_at,
        )


class ResearchProjectRunner:
    """Claim one queued project, run the lab, and persist only bound output."""

    def __init__(
        self,
        repository: ResearchProjectRepository,
        run_store: ResearchRunStore,
        lab_factory: Callable[[], ResearchLabService],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._run_store = run_store
        self._lab_factory = lab_factory
        self._clock = clock

    def run(self, project_id: str) -> ResearchProjectRecord | None:
        """Execute at most once for the repository's current queued state."""
        claimed = self._repository.claim(project_id, self._clock())
        if claimed is None:
            return None
        try:
            research_run = self._lab_factory().run(claimed.brief)
            payload = _serialize_research_run(research_run)
            object_name = self._run_store.put(project_id, payload)
            return self._repository.complete(
                project_id,
                result_object=object_name,
                result_sha256=sha256(payload).hexdigest(),
                result_size_bytes=len(payload),
                updated_at=self._clock(),
            )
        except Exception as error:
            self._repository.fail(
                project_id,
                failure_code="research_execution_failed",
                failure_message=f"Research execution failed ({type(error).__name__})",
                updated_at=self._clock(),
            )
            raise


def research_result_object_name(project_id: str) -> str:
    """Build a stable non-traversable object name from a validated entity ID."""
    normalized = project_id.strip()
    if not normalized or "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise ValueError("project_id cannot form a safe result object name")
    return f"research-projects/{normalized}/research_lab_run.json"


def _serialize_research_run(run: ResearchLabRun) -> bytes:
    payload = (
        json.dumps(
            cast(JsonValue, run.model_dump(mode="json")),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > _MAX_RESEARCH_RUN_BYTES:
        raise ValueError("research run exceeds the configured artifact size limit")
    return payload


def _reviewed_status(decision: ResearchLabReviewDecision) -> ResearchProjectStatus:
    if decision in {
        ResearchLabReviewDecision.APPROVE,
        ResearchLabReviewDecision.APPROVE_WITH_EDITS,
    }:
        return ResearchProjectStatus.APPROVED
    if decision is ResearchLabReviewDecision.REJECT:
        return ResearchProjectStatus.REJECTED
    return ResearchProjectStatus.HELD
