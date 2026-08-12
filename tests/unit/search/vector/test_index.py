"""Tests for the content-addressed chunk vector index."""

from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from defense_research_agent.search.embeddings import FakeEmbeddingProvider
from defense_research_agent.search.vector import (
    VECTOR_ENTRIES_FILENAME,
    VECTOR_INDEX_MANIFEST_VERSION,
    VECTOR_MANIFEST_FILENAME,
    VECTOR_SIMILARITY_METRIC,
    VECTOR_TIE_BREAKER,
    InMemoryVectorIndex,
    VectorIndex,
    VectorIndexBuildError,
    write_vector_index_artifacts,
)

from ._support import (
    CHUNKING_VERSION,
    StaticEmbeddingProvider,
    make_chunk,
)


def test_same_inputs_and_settings_write_byte_identical_manifest_and_index(
    tmp_path: Path,
) -> None:
    chunks = [
        make_chunk("pub:z", 0, "지휘통제 정책"),
        make_chunk("pub:a", 0, "무인체계 획득"),
        make_chunk("pub:a", 1, "인력구조 분석"),
    ]
    first_index = InMemoryVectorIndex()
    second_index = InMemoryVectorIndex()

    first_manifest = first_index.build(
        chunks,
        FakeEmbeddingProvider(dimension=7, normalized=True, max_batch_size=2),
        chunking_version=CHUNKING_VERSION,
    )
    second_manifest = second_index.build(
        list(reversed(chunks)),
        FakeEmbeddingProvider(dimension=7, normalized=True, max_batch_size=2),
        chunking_version=CHUNKING_VERSION,
    )
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    write_vector_index_artifacts(first_index, first_output)
    write_vector_index_artifacts(second_index, second_output)

    first_entries = (first_output / VECTOR_ENTRIES_FILENAME).read_bytes()
    second_entries = (second_output / VECTOR_ENTRIES_FILENAME).read_bytes()
    first_manifest_bytes = (first_output / VECTOR_MANIFEST_FILENAME).read_bytes()
    second_manifest_bytes = (second_output / VECTOR_MANIFEST_FILENAME).read_bytes()

    assert isinstance(first_index, VectorIndex)
    assert first_entries == second_entries
    assert first_manifest_bytes == second_manifest_bytes
    assert first_manifest_bytes.endswith(b"\n")
    assert first_manifest == second_manifest
    assert first_manifest.manifest_version == VECTOR_INDEX_MANIFEST_VERSION
    assert first_manifest.embedding_model_id == "fake-sha256-axis"
    assert first_manifest.embedding_version == "1.0.0"
    assert first_manifest.dimension == 7
    assert first_manifest.normalization == "l2"
    assert first_manifest.chunking_version == CHUNKING_VERSION
    assert first_manifest.input_chunk_count == 3
    assert first_manifest.indexed_chunk_count == 3
    assert first_manifest.vector_entries_sha256 == sha256(first_entries).hexdigest()
    assert first_manifest.vector_entries_size_bytes == len(first_entries)
    assert first_manifest.similarity_metric == VECTOR_SIMILARITY_METRIC
    assert first_manifest.tie_breaker == VECTOR_TIE_BREAKER


def test_manifest_content_address_changes_with_exact_chunk_input() -> None:
    provider = FakeEmbeddingProvider(dimension=3)
    first = InMemoryVectorIndex().build(
        [make_chunk("pub:a", 0, "원문 A")],
        provider,
        chunking_version=CHUNKING_VERSION,
    )
    second = InMemoryVectorIndex().build(
        [make_chunk("pub:a", 0, "원문 B")],
        provider,
        chunking_version=CHUNKING_VERSION,
    )

    assert first.input_chunks_sha256 != second.input_chunks_sha256
    assert first.vector_entries_sha256 != second.vector_entries_sha256
    assert first.content_address != second.content_address


def test_manifest_rejects_a_field_not_bound_by_its_content_address() -> None:
    manifest = InMemoryVectorIndex().build(
        [make_chunk("pub:a", 0, "원문 A")],
        FakeEmbeddingProvider(dimension=3),
        chunking_version=CHUNKING_VERSION,
    )
    payload = manifest.model_dump(mode="json")
    payload["embedding_version"] = "tampered-version"

    with pytest.raises(ValidationError, match="content_address"):
        type(manifest).model_validate(payload)


def test_empty_index_has_complete_deterministic_content_address() -> None:
    first = InMemoryVectorIndex().build(
        [],
        FakeEmbeddingProvider(dimension=4),
        chunking_version=CHUNKING_VERSION,
    )
    second = InMemoryVectorIndex().build(
        [],
        FakeEmbeddingProvider(dimension=4),
        chunking_version=CHUNKING_VERSION,
    )

    assert first == second
    assert first.input_chunk_count == 0
    assert first.indexed_chunk_count == 0
    assert first.input_chunks_sha256 == sha256(b"").hexdigest()
    assert first.vector_entries_sha256 == sha256(b"").hexdigest()


def test_writer_rejects_output_below_read_only_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    index = InMemoryVectorIndex()
    index.build(
        [make_chunk("pub:a", 0, "fixture")],
        FakeEmbeddingProvider(),
        chunking_version=CHUNKING_VERSION,
    )

    with pytest.raises(ValueError, match="outside the read-only data"):
        write_vector_index_artifacts(index, tmp_path / "data" / "vector-index")


def test_build_fails_closed_on_embedding_partial_failure() -> None:
    index = InMemoryVectorIndex()

    with pytest.raises(VectorIndexBuildError, match="failed for input positions"):
        index.build(
            [make_chunk("pub:a", 0, "fixture")],
            StaticEmbeddingProvider(fail_documents=True),
            chunking_version=CHUNKING_VERSION,
        )

    assert index.manifest is None


def test_build_rejects_chunk_text_checksum_mismatch() -> None:
    index = InMemoryVectorIndex()

    with pytest.raises(VectorIndexBuildError, match="chunk checksum"):
        index.build(
            [make_chunk("pub:a", 0, "fixture", checksum="b" * 64)],
            FakeEmbeddingProvider(),
            chunking_version=CHUNKING_VERSION,
        )


def test_build_rejects_duplicate_chunk_identity_and_position() -> None:
    duplicate_id = [
        make_chunk("pub:a", 0, "first", chunk_id="chunk:duplicate"),
        make_chunk("pub:b", 0, "second", chunk_id="chunk:duplicate"),
    ]
    duplicate_position = [
        make_chunk("pub:a", 0, "first", chunk_id="chunk:first"),
        make_chunk("pub:a", 0, "second", chunk_id="chunk:second"),
    ]

    with pytest.raises(VectorIndexBuildError, match="chunk_id must be unique"):
        InMemoryVectorIndex().build(
            duplicate_id,
            FakeEmbeddingProvider(),
            chunking_version=CHUNKING_VERSION,
        )
    with pytest.raises(VectorIndexBuildError, match="chunk_index must be unique"):
        InMemoryVectorIndex().build(
            duplicate_position,
            FakeEmbeddingProvider(),
            chunking_version=CHUNKING_VERSION,
        )


def test_build_rejects_chunking_version_mismatch() -> None:
    with pytest.raises(VectorIndexBuildError, match="chunk version"):
        InMemoryVectorIndex().build(
            [make_chunk("pub:a", 0, "fixture", chunking_version="other-chunks-v1")],
            FakeEmbeddingProvider(),
            chunking_version=CHUNKING_VERSION,
        )
