"""Repository transition tests for asynchronous research projects."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from defense_research_agent.domain import ResearchBrief
from defense_research_agent.domain.research_project import (
    ResearchLabReviewDecision,
    ResearchLabReviewEvent,
    ResearchProjectRecord,
    ResearchProjectStatus,
)
from defense_research_agent.repositories.research_projects import (
    FirestoreResearchProjectRepository,
    InMemoryResearchProjectRepository,
    ResearchProjectStateConflictError,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _record() -> ResearchProjectRecord:
    return ResearchProjectRecord(
        project_id="research-1",
        brief=ResearchBrief(
            project_id="research-1",
            question="연구 질문",
            objective="연구 목적",
            deliverables=["보고서"],
        ),
        status=ResearchProjectStatus.QUEUED,
        created_at=NOW,
        updated_at=NOW,
    )


def _exercise_repository(
    repository: InMemoryResearchProjectRepository | FirestoreResearchProjectRepository,
) -> None:
    repository.create(_record())
    dispatched = repository.record_dispatch(
        "research-1",
        "operations/1",
        NOW + timedelta(seconds=2),
    )
    assert dispatched.execution_name == "operations/1"
    with pytest.raises(ResearchProjectStateConflictError, match="dispatch"):
        repository.record_dispatch("research-1", "operations/2", NOW)

    running = repository.claim("research-1", NOW + timedelta(seconds=1))
    assert running is not None
    assert running.status is ResearchProjectStatus.RUNNING
    assert running.updated_at == NOW + timedelta(seconds=2)
    assert running.attempt_count == 1
    assert repository.claim("research-1", NOW + timedelta(seconds=2)) is None

    completed = repository.complete(
        "research-1",
        result_object="research-projects/research-1/research_lab_run.json",
        result_sha256="1" * 64,
        result_size_bytes=100,
        updated_at=NOW,
    )
    assert completed.status is ResearchProjectStatus.AWAITING_HUMAN_REVIEW
    assert completed.updated_at == NOW + timedelta(seconds=2)

    invalid_event = ResearchLabReviewEvent(
        event_id="review:invalid-sequence",
        decision=ResearchLabReviewDecision.APPROVE,
        reviewer="검토자",
        reviewed_at=NOW + timedelta(seconds=4),
        sequence=2,
    )
    with pytest.raises(ResearchProjectStateConflictError, match="sequence"):
        repository.append_review(
            "research-1",
            invalid_event,
            ResearchProjectStatus.APPROVED,
            NOW + timedelta(seconds=4),
        )

    event = ResearchLabReviewEvent(
        event_id="review:1",
        decision=ResearchLabReviewDecision.APPROVE,
        reviewer="검토자",
        reviewed_at=NOW + timedelta(seconds=4),
        sequence=1,
    )
    approved = repository.append_review(
        "research-1",
        event,
        ResearchProjectStatus.APPROVED,
        NOW + timedelta(seconds=4),
    )
    assert approved.status is ResearchProjectStatus.APPROVED
    assert approved.review_history == [event]

    with pytest.raises(ResearchProjectStateConflictError):
        repository.fail(
            "research-1",
            failure_code="late_failure",
            failure_message="must not overwrite review",
            updated_at=NOW + timedelta(seconds=5),
        )


def test_in_memory_repository_enforces_state_machine() -> None:
    _exercise_repository(InMemoryResearchProjectRepository())


class FakeSnapshot:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self._payload = payload

    @property
    def exists(self) -> bool:
        return self._payload is not None

    def to_dict(self) -> dict[str, object] | None:
        return dict(self._payload) if self._payload is not None else None


class FakeDocument:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def create(self, document_data: dict[str, object]) -> None:
        if self.payload is not None:
            raise RuntimeError("already exists")
        self.payload = dict(document_data)

    def get(self, *, transaction: object | None = None) -> FakeSnapshot:
        del transaction
        return FakeSnapshot(self.payload)

    def update(self, field_updates: dict[str, object]) -> None:
        if self.payload is None:
            raise RuntimeError("not found")
        self.payload.update(field_updates)


class FakeCollection:
    def __init__(self) -> None:
        self.documents: dict[str, FakeDocument] = {}

    def document(self, document_id: str) -> FakeDocument:
        return self.documents.setdefault(document_id, FakeDocument())


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def collection(self, collection_id: str) -> FakeCollection:
        return self.collections.setdefault(collection_id, FakeCollection())

    def transaction(self) -> "FakeTransaction":
        return FakeTransaction()


class FakeTransaction:
    def __init__(self) -> None:
        self._read_only = False
        self._max_attempts = 1
        self._id = b"fake-transaction"
        self._updates: list[tuple[FakeDocument, dict[str, object]]] = []

    def _clean_up(self) -> None:
        self._updates = []

    def _begin(self, *, retry_id: bytes | None) -> None:
        del retry_id

    def update(
        self,
        document_ref: FakeDocument,
        field_updates: dict[str, object],
    ) -> None:
        self._updates.append((document_ref, dict(field_updates)))

    def _commit(self) -> None:
        for document_ref, updates in self._updates:
            document_ref.update(updates)

    def _rollback(self) -> None:
        self._updates = []


def test_firestore_repository_serializes_and_reloads_all_transitions() -> None:
    repository = FirestoreResearchProjectRepository(
        project_id="test-project",
        client=cast(Any, FakeFirestoreClient()),
    )
    _exercise_repository(repository)
