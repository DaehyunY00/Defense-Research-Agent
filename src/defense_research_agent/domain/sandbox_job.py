"""Structured contracts for remote code-sandbox job execution."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, PositiveInt, field_validator, model_validator

from defense_research_agent.domain.common import Checksum, DomainModel, EntityId, Label
from defense_research_agent.domain.research_lab import (
    CodeSandboxCheckResult,
    CodeSandboxValidation,
)


class SandboxJobStatus(StrEnum):
    """Worker outcome independent from the Cloud Run execution state."""

    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class SandboxJobRequest(DomainModel):
    """Immutable input passed to one isolated Cloud Run Job execution."""

    schema_version: Literal["1"] = "1"
    request_id: EntityId
    bundle_object: str = Field(min_length=1, max_length=1_024)
    result_object: str = Field(min_length=1, max_length=1_024)
    bundle_sha256: Checksum
    bundle_size_bytes: PositiveInt = Field(le=20_000_000)
    validation: CodeSandboxValidation

    @field_validator("bundle_object", "result_object")
    @classmethod
    def validate_object_name(cls, value: str) -> str:
        normalized = value.strip()
        parts = normalized.split("/")
        if (
            not normalized
            or normalized.startswith("/")
            or "\\" in normalized
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("sandbox object name must be a safe relative object path")
        return normalized

    @model_validator(mode="after")
    def validate_distinct_objects(self) -> "SandboxJobRequest":
        if self.bundle_object == self.result_object:
            raise ValueError("bundle and result objects must be distinct")
        return self


class SandboxJobResultEnvelope(DomainModel):
    """Authenticated-by-location, checksum-bound worker result."""

    schema_version: Literal["1"] = "1"
    request_id: EntityId
    bundle_sha256: Checksum
    status: SandboxJobStatus
    check_result: CodeSandboxCheckResult | None = None
    failure_code: Label | None = None
    failure_message: str | None = Field(default=None, max_length=500)
    worker_version: Label

    @model_validator(mode="after")
    def validate_outcome(self) -> "SandboxJobResultEnvelope":
        if self.status is SandboxJobStatus.COMPLETED:
            if self.check_result is None:
                raise ValueError("completed sandbox job requires check_result")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("completed sandbox job cannot include worker failure")
        elif self.failure_code is None:
            raise ValueError("rejected or failed sandbox job requires failure_code")
        return self
