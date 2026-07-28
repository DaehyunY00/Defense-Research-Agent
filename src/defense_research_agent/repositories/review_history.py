"""Append-only local review history repository."""

import fcntl
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TextIO

from defense_research_agent.domain.review import ReviewEvent, ReviewSubmission
from defense_research_agent.path_safety import ensure_outside_read_only_data

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ReviewHistoryRepository:
    """Persist review decisions by appending one validated JSON line at a time."""

    def __init__(
        self,
        artifacts_root: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._artifacts_root = artifacts_root
        self._clock = clock or (lambda: datetime.now(UTC))

    def append(
        self,
        run_id: str,
        submission: ReviewSubmission,
        *,
        reviewed_at: datetime | None = None,
    ) -> ReviewEvent:
        """Append one event without rewriting or removing prior history."""
        path = self.path_for(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as history_file:
            fcntl.flock(history_file.fileno(), fcntl.LOCK_EX)
            try:
                history_file.seek(0)
                existing = _load_events(history_file, run_id)
                sequence = len(existing) + 1
                event_time = reviewed_at or self._clock()
                if existing and event_time < existing[-1].reviewed_at:
                    raise ValueError("reviewed_at must not precede the latest review event")
                event_identity = "\0".join(
                    (
                        run_id,
                        submission.candidate_id,
                        submission.decision.value,
                        str(sequence),
                        event_time.isoformat(),
                    )
                )
                event = ReviewEvent(
                    event_id=f"review:{sha256(event_identity.encode()).hexdigest()[:24]}",
                    run_id=run_id,
                    candidate_id=submission.candidate_id,
                    decision=submission.decision,
                    reviewer=submission.reviewer,
                    edits=submission.edits,
                    comment=submission.comment,
                    reviewed_at=event_time,
                    sequence=sequence,
                )
                history_file.seek(0, os.SEEK_END)
                history_file.write(event.model_dump_json() + "\n")
                history_file.flush()
                os.fsync(history_file.fileno())
                return event
            finally:
                fcntl.flock(history_file.fileno(), fcntl.LOCK_UN)

    def load(self, run_id: str) -> list[ReviewEvent]:
        """Load and validate the complete history in append order."""
        path = self.path_for(run_id)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as history_file:
            fcntl.flock(history_file.fileno(), fcntl.LOCK_SH)
            try:
                return _load_events(history_file, run_id)
            finally:
                fcntl.flock(history_file.fileno(), fcntl.LOCK_UN)

    def path_for(self, run_id: str) -> Path:
        """Resolve a safe run-local append-only JSONL path."""
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be a safe path segment")
        path = self._artifacts_root / "runs" / run_id / "review_history.jsonl"
        ensure_outside_read_only_data(path)
        return path


def _load_events(history_file: TextIO, run_id: str) -> list[ReviewEvent]:
    events: list[ReviewEvent] = []
    event_ids: set[str] = set()
    for line_number, line in enumerate(history_file, start=1):
        if not line.strip():
            continue
        try:
            event = ReviewEvent.model_validate_json(line)
        except ValueError as error:
            raise ValueError(f"invalid review history JSON on line {line_number}") from error
        if event.run_id != run_id:
            raise ValueError("review history contains a different run_id")
        if event.sequence != len(events) + 1:
            raise ValueError("review history sequence is not append-contiguous")
        if event.event_id in event_ids:
            raise ValueError("review history contains a duplicate event_id")
        if events and event.reviewed_at < events[-1].reviewed_at:
            raise ValueError("review history timestamps are not chronological")
        event_ids.add(event.event_id)
        events.append(event)
    return events
