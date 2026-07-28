"""Common interface and source records for read-only publication readers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import ClassVar

from defense_research_agent.domain import JsonObject


class SourceFileKind(StrEnum):
    """Supported source file kinds observed in the current dataset."""

    DOCUMENT_JSON = "document_json"
    PDF = "pdf"


@dataclass(frozen=True, slots=True)
class PublicationSource:
    """A minimally interpreted, loss-aware record returned by a file reader."""

    source_path: Path
    relative_path: Path
    kind: SourceFileKind
    checksum: str
    target_filename: str
    raw_metadata: JsonObject
    content: str | None = None
    created_at: datetime | None = None


class SkipSourceFile(Exception):
    """Signal that a valid file is intentionally not a publication record."""


class PublicationReader(ABC):
    """Shared interface implemented once per observed source file format."""

    name: ClassVar[str]
    suffixes: ClassVar[frozenset[str]]

    def supports(self, path: Path) -> bool:
        """Return whether this reader owns the file extension."""
        return path.suffix.casefold() in self.suffixes

    @abstractmethod
    def read(self, path: Path, input_root: Path) -> PublicationSource:
        """Read one source without modifying it."""


def calculate_file_checksum(path: Path) -> str:
    """Calculate a streaming SHA-256 checksum without loading the file at once."""
    digest = sha256()
    with path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
