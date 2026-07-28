"""Human-reviewed internal corpus index manifest."""

from datetime import datetime
from typing import Literal

from pydantic import PositiveInt, field_validator

from defense_research_agent.domain.common import Checksum, DomainModel, Label


class CorpusIndexManifest(DomainModel):
    """Bind one immutable public corpus index to an explicit human review."""

    schema_version: Literal["1"] = "1"
    index_object: Label
    index_sha256: Checksum
    index_size_bytes: PositiveInt
    publication_count: PositiveInt
    reviewed_by: Label
    reviewed_at: datetime
    review_status: Literal["approved"] = "approved"
    data_sensitivity: Literal["public_only"] = "public_only"

    @field_validator("index_object")
    @classmethod
    def validate_index_object(cls, value: str) -> str:
        normalized = value.strip()
        parts = normalized.split("/")
        if (
            not normalized.startswith("corpus/indexes/")
            or not normalized.endswith(".jsonl")
            or "\\" in normalized
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("index_object must be a safe corpus/indexes JSONL object")
        return normalized

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return value
