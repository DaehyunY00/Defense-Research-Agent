"""Read-only model conversion tests against representative source JSON records."""

import json
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from typing import TypedDict, cast

import pytest

from defense_research_agent.domain import JsonObject, PublicationType, ResearchPublication

PROJECT_ROOT = Path(__file__).parents[2]
METADATA_DIR = PROJECT_ROOT / "data" / "metadata"
REPRESENTATIVE_CATEGORIES = {"Brief", "국방정책연구", "연구보고서"}
SOURCE_FOLDERS = {
    "Brief": "Brief",
    "국방정책연구": "국방정책연구",
    "연구보고서": "연구보고서",
}


class SourceMetadata(TypedDict):
    """Observed metadata fields in document-level JSON files."""

    filename: str
    path: str
    category: str
    num_pages: int
    file_size_mb: float
    processed_date: str
    total_chars: int
    avg_chars_per_page: int


class SourceRecord(TypedDict):
    """Observed top-level document JSON structure."""

    metadata: SourceMetadata
    full_text: str
    page_texts: list[dict[str, object]]


def _iter_source_records() -> Iterator[tuple[Path, SourceRecord]]:
    for source_path in sorted(METADATA_DIR.glob("*.json")):
        payload = cast(dict[str, object], json.loads(source_path.read_text(encoding="utf-8")))
        if not isinstance(payload.get("metadata"), dict):
            continue
        yield source_path, cast(SourceRecord, payload)


@pytest.fixture(scope="module")
def representative_records() -> list[tuple[Path, SourceRecord]]:
    """Select one actual record for three observed publication categories."""
    selected: dict[str, tuple[Path, SourceRecord]] = {}
    for source_path, record in _iter_source_records():
        category = record["metadata"]["category"]
        if category in REPRESENTATIVE_CATEGORIES and category not in selected:
            selected[category] = (source_path, record)
        if selected.keys() == REPRESENTATIVE_CATEGORIES:
            break

    assert selected.keys() == REPRESENTATIVE_CATEGORIES
    return [selected[category] for category in sorted(selected)]


def _to_publication(source_path: Path, record: SourceRecord) -> ResearchPublication:
    source_checksum = sha256(source_path.read_bytes()).hexdigest()
    metadata = record["metadata"]
    return ResearchPublication.model_validate(
        {
            "publication_id": f"pub:fixture:{source_checksum[:24]}",
            "publication_type": metadata["category"],
            "local_path": str(
                Path("data") / SOURCE_FOLDERS[metadata["category"]] / metadata["filename"]
            ),
            "raw_metadata": cast(JsonObject, dict(metadata)),
            "content": record["full_text"],
            "created_at": metadata["processed_date"],
            "checksum": source_checksum,
        }
    )


def test_representative_records_convert_without_inventing_missing_fields(
    representative_records: list[tuple[Path, SourceRecord]],
) -> None:
    publications = [
        _to_publication(source_path, record) for source_path, record in representative_records
    ]

    assert {publication.publication_type for publication in publications} == {
        PublicationType.KIDA_BRIEF,
        PublicationType.DEFENSE_POLICY_RESEARCH,
        PublicationType.RESEARCH_REPORT,
    }
    for publication, (_, record) in zip(publications, representative_records, strict=True):
        assert publication.title is None
        assert publication.authors == []
        assert publication.publication_date is None
        assert publication.raw_metadata["filename"] == record["metadata"]["filename"]
        assert publication.content == record["full_text"]
        assert publication.created_at is not None

        encoded = publication.model_dump_json()
        assert ResearchPublication.model_validate_json(encoded) == publication
        assert any("가" <= character <= "힣" for character in encoded)
