"""Reader for the observed UTF-8 document metadata JSON format."""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from defense_research_agent.data.readers.base import (
    PublicationReader,
    PublicationSource,
    SkipSourceFile,
    SourceFileKind,
    calculate_file_checksum,
)
from defense_research_agent.domain import JsonObject


class _DocumentEnvelope(BaseModel):
    """Observed document JSON envelope; unknown fields remain allowed."""

    model_config = ConfigDict(extra="allow")

    metadata: JsonObject
    full_text: str
    page_texts: list[dict[str, JsonValue]]


class JsonPublicationReader(PublicationReader):
    """Read document JSON records and reject malformed UTF-8 or schemas."""

    name = "json"
    suffixes = frozenset({".json"})

    def read(self, path: Path, input_root: Path) -> PublicationSource:
        """Read one observed document record using strict UTF-8 decoding."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        if self._is_aggregate_index(payload):
            raise SkipSourceFile("aggregate JSON index is not a publication record")

        try:
            document = _DocumentEnvelope.model_validate(payload)
        except ValidationError as error:
            raise ValueError("JSON does not match the document record schema") from error

        filename = document.metadata.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("metadata.filename must be a non-empty string")

        created_at = self._parse_processed_date(document.metadata)
        return PublicationSource(
            source_path=path,
            relative_path=path.relative_to(input_root),
            kind=SourceFileKind.DOCUMENT_JSON,
            checksum=calculate_file_checksum(path),
            target_filename=filename,
            raw_metadata=document.metadata,
            content=document.full_text,
            created_at=created_at,
        )

    @staticmethod
    def _is_aggregate_index(payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        return "documents" in payload and "total_documents" in payload and "metadata" not in payload

    @staticmethod
    def _parse_processed_date(metadata: JsonObject) -> datetime | None:
        processed_date = metadata.get("processed_date")
        if processed_date is None:
            return None
        if not isinstance(processed_date, str):
            raise ValueError("metadata.processed_date must be an ISO datetime string")
        try:
            return datetime.fromisoformat(processed_date)
        except ValueError as error:
            raise ValueError("metadata.processed_date is not a valid ISO datetime") from error
