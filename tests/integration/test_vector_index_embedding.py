"""Provider-index integration tests with corpus-representative UTF-8 sizes."""

from hashlib import sha256
from pathlib import Path

import pytest

from defense_research_agent.domain import (
    ExtractionProvenance,
    PublicationChunk,
    PublicationPageSpan,
)
from defense_research_agent.search.embeddings import (
    EmbeddingErrorCode,
    FakeEmbeddingProvider,
)
from defense_research_agent.search.vector import (
    VECTOR_ENTRIES_FILENAME,
    VECTOR_MANIFEST_FILENAME,
    InMemoryVectorIndex,
    VectorIndexManifest,
    write_vector_index_artifacts,
)

CHUNKING_VERSION = "fixture-chunks-v1"
MEDIAN_SIZE_BYTES = 7_073
P90_SIZE_BYTES = 8_712
MAX_SIZE_BYTES = 10_212
SUCCESSFUL_CHUNK_COUNT = 17
OVERSIZED_CHUNK_COUNT = 15
PROVENANCE = ExtractionProvenance(
    parser_name="generated-size-fixture",
    parser_version="1.0.0",
    source_checksum="a" * 64,
)


def _text_with_utf8_size(size_bytes: int, fixture_index: int) -> str:
    prefix = f"{fixture_index:02d}:"
    korean_count, ascii_count = divmod(size_bytes - len(prefix), 3)
    text = prefix + ("가" * korean_count) + ("x" * ascii_count)
    assert len(text.encode("utf-8")) == size_bytes
    return text


def _chunk(fixture_index: int, size_bytes: int) -> PublicationChunk:
    text = _text_with_utf8_size(size_bytes, fixture_index)
    return PublicationChunk(
        chunk_id=f"chunk:realistic:{fixture_index:02d}",
        publication_id=f"pub:realistic:{fixture_index:02d}",
        text=text,
        page_start=1,
        page_end=1,
        page_spans=[
            PublicationPageSpan(
                page_number=1,
                start_offset=0,
                end_offset=len(text),
            )
        ],
        provenance=PROVENANCE,
        chunk_index=0,
        checksum=sha256(text.encode("utf-8")).hexdigest(),
        chunking_version=CHUNKING_VERSION,
    )


@pytest.fixture
def corpus_representative_batch() -> list[PublicationChunk]:
    """Generate the measured 32-item batch shape without reading corpus artifacts."""
    sizes = (
        [MEDIAN_SIZE_BYTES] * SUCCESSFUL_CHUNK_COUNT
        + [P90_SIZE_BYTES] * (OVERSIZED_CHUNK_COUNT - 1)
        + [MAX_SIZE_BYTES]
    )
    return [_chunk(fixture_index, size) for fixture_index, size in enumerate(sizes)]


def test_realistic_mixed_batch_builds_auditable_partial_index(
    corpus_representative_batch: list[PublicationChunk],
    tmp_path: Path,
) -> None:
    provider = FakeEmbeddingProvider(normalized=True)
    raw_result = provider.embed_documents([chunk.text for chunk in corpus_representative_batch])
    index = InMemoryVectorIndex()
    reversed_index = InMemoryVectorIndex()

    manifest = index.build(
        corpus_representative_batch,
        provider,
        chunking_version=CHUNKING_VERSION,
    )
    reversed_manifest = reversed_index.build(
        list(reversed(corpus_representative_batch)),
        FakeEmbeddingProvider(normalized=True),
        chunking_version=CHUNKING_VERSION,
    )
    write_vector_index_artifacts(index, tmp_path)
    persisted_manifest = VectorIndexManifest.model_validate_json(
        (tmp_path / VECTOR_MANIFEST_FILENAME).read_bytes()
    )

    assert len(corpus_representative_batch) == provider.max_batch_size == 32
    assert len(raw_result.vectors) == SUCCESSFUL_CHUNK_COUNT
    assert len(raw_result.failures) == OVERSIZED_CHUNK_COUNT
    assert manifest == reversed_manifest
    assert index.canonical_entries() == reversed_index.canonical_entries()
    assert manifest == persisted_manifest
    assert manifest.input_chunk_count == 32
    assert manifest.indexed_chunk_count == SUCCESSFUL_CHUNK_COUNT
    assert manifest.skipped_chunk_count == OVERSIZED_CHUNK_COUNT
    assert len((tmp_path / VECTOR_ENTRIES_FILENAME).read_bytes().splitlines()) == 17
    assert {skipped.failure_code for skipped in manifest.skipped_chunks} == {
        EmbeddingErrorCode.INPUT_TOO_LONG
    }
    assert [skipped.input_size_bytes for skipped in manifest.skipped_chunks] == [
        *([P90_SIZE_BYTES] * (OVERSIZED_CHUNK_COUNT - 1)),
        MAX_SIZE_BYTES,
    ]
    assert all(
        f"maximum is {provider.max_input_bytes}" in skipped.failure_message
        for skipped in manifest.skipped_chunks
    )
    assert (
        len(index.nearest(raw_result.vectors[0].values, allowed_publication_ids=None, limit=5)) == 5
    )
