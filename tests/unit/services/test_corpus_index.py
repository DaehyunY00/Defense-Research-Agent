"""Tests for reviewed corpus manifest generation."""

from datetime import UTC, datetime

import pytest

from defense_research_agent.domain import PublicationType, ResearchPublication
from defense_research_agent.services import (
    build_corpus_index_manifest,
    corpus_manifest_object_name,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _publication_line(publication_id: str) -> bytes:
    publication = ResearchPublication(
        publication_id=publication_id,
        publication_type=PublicationType.KIDA_BRIEF,
        title="검토된 공개자료",
    )
    return f"{publication.model_dump_json()}\n".encode()


def test_manifest_binds_digest_count_reviewer_and_content_addressed_names() -> None:
    payload = _publication_line("pub:one")

    manifest = build_corpus_index_manifest(
        payload,
        reviewed_by="reviewer@example.com",
        reviewed_at=NOW,
    )

    assert manifest.index_size_bytes == len(payload)
    assert manifest.publication_count == 1
    assert manifest.reviewed_by == "reviewer@example.com"
    assert manifest.index_object.endswith(f"{manifest.index_sha256}.jsonl")
    assert corpus_manifest_object_name(manifest).endswith(f"{manifest.index_sha256}.json")


def test_manifest_rejects_duplicate_publication_ids_and_naive_review_time() -> None:
    duplicate = _publication_line("pub:one") * 2

    with pytest.raises(ValueError, match="duplicate publication_id"):
        build_corpus_index_manifest(
            duplicate,
            reviewed_by="reviewer@example.com",
            reviewed_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        build_corpus_index_manifest(
            _publication_line("pub:one"),
            reviewed_by="reviewer@example.com",
            reviewed_at=datetime(2026, 7, 28),
        )
