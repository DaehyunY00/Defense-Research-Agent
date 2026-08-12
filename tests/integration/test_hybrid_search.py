"""Offline lexical/vector hybrid integration with corpus-representative chunks."""

from hashlib import sha256

from defense_research_agent.domain import (
    ExtractionProvenance,
    PublicationChunk,
    PublicationPageSpan,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.search.embeddings import FakeEmbeddingProvider
from defense_research_agent.search.hybrid import (
    HybridSearchAlgorithm,
    HybridSearchStatus,
    HybridVectorCoverageStatus,
)
from defense_research_agent.search.lexical import LocalLexicalSearchAlgorithm
from defense_research_agent.search.vector import VectorSearchAlgorithm

CHUNKING_VERSION = "hybrid-realistic-fixture-v1"
MEDIAN_CHUNK_SIZE_BYTES = 7_073
OVERSIZED_CHUNK_SIZE_BYTES = 8_712
PROVENANCE = ExtractionProvenance(
    parser_name="generated-hybrid-fixture",
    parser_version="1.0.0",
    source_checksum="b" * 64,
)


def _text_with_utf8_size(size_bytes: int, fixture_index: int) -> str:
    prefix = f"방위 {fixture_index:02d}:"
    prefix_size = len(prefix.encode("utf-8"))
    korean_count, ascii_count = divmod(size_bytes - prefix_size, 3)
    text = prefix + ("가" * korean_count) + ("x" * ascii_count)
    assert len(text.encode("utf-8")) == size_bytes
    return text


def _chunk(publication_id: str, text: str) -> PublicationChunk:
    return PublicationChunk(
        chunk_id=f"chunk:{publication_id.removeprefix('pub:')}:0",
        publication_id=publication_id,
        text=text,
        page_start=1,
        page_end=1,
        page_spans=[PublicationPageSpan(page_number=1, start_offset=0, end_offset=len(text))],
        provenance=PROVENANCE,
        chunk_index=0,
        checksum=sha256(text.encode("utf-8")).hexdigest(),
        chunking_version=CHUNKING_VERSION,
    )


def test_realistic_chunk_sizes_flow_through_both_searches_with_visible_partial_coverage() -> None:
    sizes = [MEDIAN_CHUNK_SIZE_BYTES] * 4 + [OVERSIZED_CHUNK_SIZE_BYTES]
    texts = [_text_with_utf8_size(size, index) for index, size in enumerate(sizes)]
    publication_ids = [f"pub:hybrid:{index:02d}" for index in range(len(texts))]
    publications = [
        ResearchPublication(
            publication_id=publication_id,
            publication_type=PublicationType.RESEARCH_REPORT,
            title=f"방위 연구 {index}",
            content=text,
        )
        for index, (publication_id, text) in enumerate(zip(publication_ids, texts, strict=True))
    ]
    chunks = [
        _chunk(publication_id, text)
        for publication_id, text in zip(publication_ids, texts, strict=True)
    ]
    lexical = LocalLexicalSearchAlgorithm()
    lexical.build_index(publications)
    vector = VectorSearchAlgorithm(
        FakeEmbeddingProvider(normalized=True),
        chunking_version=CHUNKING_VERSION,
    )
    manifest = vector.build_index(chunks)
    algorithm = HybridSearchAlgorithm(lexical, vector)

    first = algorithm.search("방위", None, 5)
    second = algorithm.search("방위", None, 5)
    by_id = {match.publication_id: match for match in first.matches}

    assert manifest.input_chunk_count == 5
    assert manifest.indexed_chunk_count == 4
    assert manifest.skipped_chunk_count == 1
    assert first.status is HybridSearchStatus.FUSED
    assert first.vector_index is not None
    assert first.vector_index.skipped_chunk_count == 1
    assert first.lexical_candidate_count == 5
    assert first.lexical_candidates_truncated is False
    assert first.vector_publication_candidate_count == 4
    assert first.vector_publication_candidates_truncated is False
    assert first.fusion_candidate_count == 5
    assert len(first.matches) == 5
    assert all(match.lexical_score is not None for match in first.matches)
    assert all(
        match.vector_chunk is None
        or len(match.vector_chunk.text.encode("utf-8")) == MEDIAN_CHUNK_SIZE_BYTES
        for match in first.matches
    )
    missing_vector = by_id[publication_ids[-1]]
    assert missing_vector.vector_rank is None
    assert missing_vector.vector_coverage_status is HybridVectorCoverageStatus.NOT_INDEXED
    assert missing_vector.vector_observed_publication_rank is None
    assert missing_vector.vector_skipped_chunk_count == 1
    assert by_id[publication_ids[0]].vector_coverage_status is (HybridVectorCoverageStatus.RANKED)
    assert by_id[publication_ids[0]].vector_skipped_chunk_count == 0
    assert first.model_dump_json().encode("utf-8") == second.model_dump_json().encode("utf-8")
