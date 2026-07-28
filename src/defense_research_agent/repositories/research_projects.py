"""Research project persistence contracts and Firestore adapter."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from threading import Lock
from typing import Protocol, cast

from google.cloud import firestore
from google.cloud.firestore_v1.transaction import Transaction

from defense_research_agent.domain.research_project import (
    ResearchLabReviewDecision,
    ResearchLabReviewEvent,
    ResearchProjectRecord,
    ResearchProjectStatus,
)


class ResearchProjectAlreadyExistsError(ValueError):
    """Raised when a generated project ID unexpectedly already exists."""


class ResearchProjectNotFoundError(LookupError):
    """Raised when a project ID is absent from the configured repository."""


class ResearchProjectStateConflictError(ValueError):
    """Raised when an operation is invalid for the current project state."""


class ResearchProjectRepository(ABC):
    """Persistence boundary for deterministic research-project transitions."""

    @abstractmethod
    def create(self, record: ResearchProjectRecord) -> None:
        """Create one project without overwriting an existing record."""

    @abstractmethod
    def get(self, project_id: str) -> ResearchProjectRecord | None:
        """Return one project or ``None``."""

    @abstractmethod
    def record_dispatch(
        self,
        project_id: str,
        execution_name: str,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        """Attach the Cloud Run operation name without changing lifecycle state."""

    @abstractmethod
    def claim(self, project_id: str, updated_at: datetime) -> ResearchProjectRecord | None:
        """Move queued work to running, or return ``None`` when already claimed."""

    @abstractmethod
    def complete(
        self,
        project_id: str,
        *,
        result_object: str,
        result_sha256: str,
        result_size_bytes: int,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        """Move running work to the human-review boundary."""

    @abstractmethod
    def fail(
        self,
        project_id: str,
        *,
        failure_code: str,
        failure_message: str,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        """Persist one sanitized terminal failure."""

    @abstractmethod
    def append_review(
        self,
        project_id: str,
        event: ResearchLabReviewEvent,
        status: ResearchProjectStatus,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        """Append a human event and transition to its reviewed status."""


class InMemoryResearchProjectRepository(ResearchProjectRepository):
    """Thread-safe deterministic repository used by API and worker tests."""

    def __init__(self) -> None:
        self._records: dict[str, ResearchProjectRecord] = {}
        self._lock = Lock()

    def create(self, record: ResearchProjectRecord) -> None:
        with self._lock:
            if record.project_id in self._records:
                raise ResearchProjectAlreadyExistsError(record.project_id)
            self._records[record.project_id] = record.model_copy(deep=True)

    def get(self, project_id: str) -> ResearchProjectRecord | None:
        with self._lock:
            record = self._records.get(project_id)
            return record.model_copy(deep=True) if record is not None else None

    def record_dispatch(
        self,
        project_id: str,
        execution_name: str,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        with self._lock:
            current = self._required(project_id)
            if current.execution_name == execution_name:
                return current.model_copy(deep=True)
            if current.execution_name is not None:
                raise ResearchProjectStateConflictError(
                    "project already has a different dispatch execution"
                )
            _validate_aware_time(updated_at)
            updated = _validated_update(
                current,
                execution_name=execution_name,
                updated_at=max(current.updated_at, updated_at),
                revision=current.revision + 1,
            )
            self._records[project_id] = updated
            return updated.model_copy(deep=True)

    def claim(self, project_id: str, updated_at: datetime) -> ResearchProjectRecord | None:
        with self._lock:
            current = self._required(project_id)
            if current.status is not ResearchProjectStatus.QUEUED:
                return None
            effective_updated_at = _effective_transition_time(current, updated_at)
            updated = _validated_update(
                current,
                status=ResearchProjectStatus.RUNNING,
                updated_at=effective_updated_at,
                attempt_count=current.attempt_count + 1,
                revision=current.revision + 1,
            )
            self._records[project_id] = updated
            return updated.model_copy(deep=True)

    def complete(
        self,
        project_id: str,
        *,
        result_object: str,
        result_sha256: str,
        result_size_bytes: int,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        with self._lock:
            current = self._required_status(project_id, ResearchProjectStatus.RUNNING)
            effective_updated_at = _effective_transition_time(current, updated_at)
            updated = _validated_update(
                current,
                status=ResearchProjectStatus.AWAITING_HUMAN_REVIEW,
                result_object=result_object,
                result_sha256=result_sha256,
                result_size_bytes=result_size_bytes,
                updated_at=effective_updated_at,
                revision=current.revision + 1,
            )
            self._records[project_id] = updated
            return updated.model_copy(deep=True)

    def fail(
        self,
        project_id: str,
        *,
        failure_code: str,
        failure_message: str,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        with self._lock:
            current = self._required(project_id)
            if current.status not in {
                ResearchProjectStatus.QUEUED,
                ResearchProjectStatus.RUNNING,
            }:
                raise ResearchProjectStateConflictError(
                    f"cannot fail {current.status.value} project"
                )
            effective_updated_at = _effective_transition_time(current, updated_at)
            updated = _validated_update(
                current,
                status=ResearchProjectStatus.FAILED,
                failure_code=failure_code,
                failure_message=failure_message,
                updated_at=effective_updated_at,
                revision=current.revision + 1,
            )
            self._records[project_id] = updated
            return updated.model_copy(deep=True)

    def append_review(
        self,
        project_id: str,
        event: ResearchLabReviewEvent,
        status: ResearchProjectStatus,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        with self._lock:
            current = self._required(project_id)
            _validate_review_transition(current, event, status, updated_at)
            updated = _validated_update(
                current,
                status=status,
                review_history=[*current.review_history, event],
                updated_at=updated_at,
                revision=current.revision + 1,
            )
            self._records[project_id] = updated
            return updated.model_copy(deep=True)

    def _required(self, project_id: str) -> ResearchProjectRecord:
        record = self._records.get(project_id)
        if record is None:
            raise ResearchProjectNotFoundError(project_id)
        return record

    def _required_status(
        self,
        project_id: str,
        status: ResearchProjectStatus,
    ) -> ResearchProjectRecord:
        record = self._required(project_id)
        if record.status is not status:
            raise ResearchProjectStateConflictError(
                f"project status must be {status.value}, got {record.status.value}"
            )
        return record


class _DocumentSnapshot(Protocol):
    @property
    def exists(self) -> bool: ...

    def to_dict(self) -> dict[str, object] | None: ...


class _DocumentReference(Protocol):
    def create(self, document_data: Mapping[str, object]) -> object: ...

    def get(self, *, transaction: object | None = None) -> _DocumentSnapshot: ...

    def update(self, field_updates: Mapping[str, object]) -> object: ...


class _Transaction(Protocol):
    def update(
        self,
        document_ref: _DocumentReference,
        field_updates: Mapping[str, object],
    ) -> object: ...


class _CollectionReference(Protocol):
    def document(self, document_id: str) -> _DocumentReference: ...


class _FirestoreClient(Protocol):
    def collection(self, collection_id: str) -> _CollectionReference: ...

    def transaction(self) -> _Transaction: ...


def _record_dispatch_in_transaction(
    transaction: _Transaction,
    document: _DocumentReference,
    execution_name: str,
    updated_at: datetime,
) -> ResearchProjectRecord:
    current = _transaction_record(transaction, document)
    if current.execution_name == execution_name:
        return current
    if current.execution_name is not None:
        raise ResearchProjectStateConflictError(
            "project already has a different dispatch execution"
        )
    _validate_aware_time(updated_at)
    effective_updated_at = max(current.updated_at, updated_at)
    updated = _validated_update(
        current,
        execution_name=execution_name,
        updated_at=effective_updated_at,
        revision=current.revision + 1,
    )
    transaction.update(
        document,
        {
            "execution_name": updated.execution_name,
            "updated_at": updated.updated_at.isoformat(),
            "revision": updated.revision,
        },
    )
    return updated


def _claim_in_transaction(
    transaction: _Transaction,
    document: _DocumentReference,
    updated_at: datetime,
) -> ResearchProjectRecord | None:
    current = _transaction_record(transaction, document)
    if current.status is not ResearchProjectStatus.QUEUED:
        return None
    effective_updated_at = _effective_transition_time(current, updated_at)
    updated = _validated_update(
        current,
        status=ResearchProjectStatus.RUNNING,
        updated_at=effective_updated_at,
        attempt_count=current.attempt_count + 1,
        revision=current.revision + 1,
    )
    transaction.update(
        document,
        {
            "status": updated.status.value,
            "updated_at": updated.updated_at.isoformat(),
            "attempt_count": updated.attempt_count,
            "revision": updated.revision,
        },
    )
    return updated


def _complete_in_transaction(
    transaction: _Transaction,
    document: _DocumentReference,
    result_object: str,
    result_sha256: str,
    result_size_bytes: int,
    updated_at: datetime,
) -> ResearchProjectRecord:
    current = _transaction_record(transaction, document)
    if current.status is not ResearchProjectStatus.RUNNING:
        raise ResearchProjectStateConflictError("project status must be running before completion")
    effective_updated_at = _effective_transition_time(current, updated_at)
    updated = _validated_update(
        current,
        status=ResearchProjectStatus.AWAITING_HUMAN_REVIEW,
        result_object=result_object,
        result_sha256=result_sha256,
        result_size_bytes=result_size_bytes,
        updated_at=effective_updated_at,
        revision=current.revision + 1,
    )
    transaction.update(
        document,
        {
            "status": updated.status.value,
            "result_object": updated.result_object,
            "result_sha256": updated.result_sha256,
            "result_size_bytes": updated.result_size_bytes,
            "updated_at": updated.updated_at.isoformat(),
            "revision": updated.revision,
        },
    )
    return updated


def _fail_in_transaction(
    transaction: _Transaction,
    document: _DocumentReference,
    failure_code: str,
    failure_message: str,
    updated_at: datetime,
) -> ResearchProjectRecord:
    current = _transaction_record(transaction, document)
    if current.status not in {
        ResearchProjectStatus.QUEUED,
        ResearchProjectStatus.RUNNING,
    }:
        raise ResearchProjectStateConflictError(f"cannot fail {current.status.value} project")
    effective_updated_at = _effective_transition_time(current, updated_at)
    updated = _validated_update(
        current,
        status=ResearchProjectStatus.FAILED,
        failure_code=failure_code,
        failure_message=failure_message,
        updated_at=effective_updated_at,
        revision=current.revision + 1,
    )
    transaction.update(
        document,
        {
            "status": updated.status.value,
            "failure_code": updated.failure_code,
            "failure_message": updated.failure_message,
            "updated_at": updated.updated_at.isoformat(),
            "revision": updated.revision,
        },
    )
    return updated


def _append_review_in_transaction(
    transaction: _Transaction,
    document: _DocumentReference,
    event: ResearchLabReviewEvent,
    status: ResearchProjectStatus,
    updated_at: datetime,
) -> ResearchProjectRecord:
    current = _transaction_record(transaction, document)
    _validate_review_transition(current, event, status, updated_at)
    updated = _validated_update(
        current,
        status=status,
        review_history=[*current.review_history, event],
        updated_at=updated_at,
        revision=current.revision + 1,
    )
    transaction.update(
        document,
        {
            "status": updated.status.value,
            "review_history": [item.model_dump(mode="json") for item in updated.review_history],
            "updated_at": updated.updated_at.isoformat(),
            "revision": updated.revision,
        },
    )
    return updated


_transactional_record_dispatch = firestore.transactional(_record_dispatch_in_transaction)
_transactional_claim = firestore.transactional(_claim_in_transaction)
_transactional_complete = firestore.transactional(_complete_in_transaction)
_transactional_fail = firestore.transactional(_fail_in_transaction)
_transactional_append_review = firestore.transactional(_append_review_in_transaction)


class FirestoreResearchProjectRepository(ResearchProjectRepository):
    """Firestore persistence with field-only updates for independent transitions."""

    def __init__(
        self,
        *,
        project_id: str,
        database: str = "(default)",
        collection: str = "research_projects",
        client: _FirestoreClient | None = None,
    ) -> None:
        if not project_id.strip() or not database.strip() or not collection.strip():
            raise ValueError("Firestore project, database, and collection are required")
        if client is None:
            client = cast(
                _FirestoreClient,
                firestore.Client(project=project_id, database=database),
            )
        self._client = client
        self._collection = client.collection(collection)

    def create(self, record: ResearchProjectRecord) -> None:
        try:
            self._document(record.project_id).create(_record_payload(record))
        except Exception as error:
            if type(error).__name__ in {"AlreadyExists", "Conflict"}:
                raise ResearchProjectAlreadyExistsError(record.project_id) from error
            raise

    def get(self, project_id: str) -> ResearchProjectRecord | None:
        snapshot = self._document(project_id).get()
        if not snapshot.exists:
            return None
        payload = snapshot.to_dict()
        if payload is None:
            return None
        return ResearchProjectRecord.model_validate(payload)

    def record_dispatch(
        self,
        project_id: str,
        execution_name: str,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        result = _transactional_record_dispatch(
            cast(Transaction, self._client.transaction()),
            self._document(project_id),
            execution_name,
            updated_at,
        )
        return cast(ResearchProjectRecord, result)

    def claim(self, project_id: str, updated_at: datetime) -> ResearchProjectRecord | None:
        result = _transactional_claim(
            cast(Transaction, self._client.transaction()),
            self._document(project_id),
            updated_at,
        )
        return cast(ResearchProjectRecord | None, result)

    def complete(
        self,
        project_id: str,
        *,
        result_object: str,
        result_sha256: str,
        result_size_bytes: int,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        result = _transactional_complete(
            cast(Transaction, self._client.transaction()),
            self._document(project_id),
            result_object,
            result_sha256,
            result_size_bytes,
            updated_at,
        )
        return cast(ResearchProjectRecord, result)

    def fail(
        self,
        project_id: str,
        *,
        failure_code: str,
        failure_message: str,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        result = _transactional_fail(
            cast(Transaction, self._client.transaction()),
            self._document(project_id),
            failure_code,
            failure_message,
            updated_at,
        )
        return cast(ResearchProjectRecord, result)

    def append_review(
        self,
        project_id: str,
        event: ResearchLabReviewEvent,
        status: ResearchProjectStatus,
        updated_at: datetime,
    ) -> ResearchProjectRecord:
        result = _transactional_append_review(
            cast(Transaction, self._client.transaction()),
            self._document(project_id),
            event,
            status,
            updated_at,
        )
        return cast(ResearchProjectRecord, result)

    def _document(self, project_id: str) -> _DocumentReference:
        return self._collection.document(project_id)

    def _required(self, project_id: str) -> ResearchProjectRecord:
        record = self.get(project_id)
        if record is None:
            raise ResearchProjectNotFoundError(project_id)
        return record


def _record_payload(record: ResearchProjectRecord) -> dict[str, object]:
    payload = record.model_dump(mode="json")
    return cast(dict[str, object], deepcopy(payload))


def _transaction_record(
    transaction: _Transaction,
    document: _DocumentReference,
) -> ResearchProjectRecord:
    snapshot = document.get(transaction=transaction)
    if not snapshot.exists:
        raise ResearchProjectNotFoundError("research project not found")
    payload = snapshot.to_dict()
    if payload is None:
        raise ResearchProjectNotFoundError("research project has no payload")
    return ResearchProjectRecord.model_validate(payload)


def _validated_update(
    current: ResearchProjectRecord,
    **updates: object,
) -> ResearchProjectRecord:
    payload = cast(dict[str, object], current.model_dump(mode="python"))
    payload.update(updates)
    return ResearchProjectRecord.model_validate(payload)


def _validate_transition_time(
    current: ResearchProjectRecord,
    updated_at: datetime,
) -> None:
    _validate_aware_time(updated_at)
    if updated_at < current.updated_at:
        raise ResearchProjectStateConflictError(
            "transition timestamp cannot precede the current project update"
        )


def _effective_transition_time(
    current: ResearchProjectRecord,
    updated_at: datetime,
) -> datetime:
    """Keep automated distributed transitions monotonic despite minor clock skew."""
    _validate_aware_time(updated_at)
    return max(current.updated_at, updated_at)


def _validate_aware_time(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("transition timestamp must be timezone-aware")


def _validate_review_transition(
    current: ResearchProjectRecord,
    event: ResearchLabReviewEvent,
    status: ResearchProjectStatus,
    updated_at: datetime,
) -> None:
    if current.status not in {
        ResearchProjectStatus.AWAITING_HUMAN_REVIEW,
        ResearchProjectStatus.HELD,
    }:
        raise ResearchProjectStateConflictError(
            "project must be awaiting_human_review or held before review"
        )
    expected_status = (
        ResearchProjectStatus.APPROVED
        if event.decision
        in {
            ResearchLabReviewDecision.APPROVE,
            ResearchLabReviewDecision.APPROVE_WITH_EDITS,
        }
        else ResearchProjectStatus.REJECTED
        if event.decision is ResearchLabReviewDecision.REJECT
        else ResearchProjectStatus.HELD
    )
    if status is not expected_status:
        raise ValueError("review transition target does not match the human decision")
    expected_sequence = len(current.review_history) + 1
    if event.sequence != expected_sequence:
        raise ResearchProjectStateConflictError(
            f"review sequence must be {expected_sequence}, got {event.sequence}"
        )
    if any(existing.event_id == event.event_id for existing in current.review_history):
        raise ResearchProjectStateConflictError("review event ID already exists")
    if event.reviewed_at != updated_at:
        raise ValueError("review event and project update timestamps must match")
    _validate_transition_time(current, updated_at)
