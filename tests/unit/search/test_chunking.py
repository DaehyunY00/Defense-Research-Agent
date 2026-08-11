"""Tests for deterministic page-aware publication chunking."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from defense_research_agent.domain import (
    ExtractionProvenance,
    PublicationPage,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.search import DeterministicPageChunker, PublicationChunker
from defense_research_agent.search.chunking import (
    CHUNK_MANIFEST_FILENAME,
    CHUNK_MANIFEST_VERSION,
    CHUNKS_FILENAME,
    ChunkArtifactManifest,
    ChunkingDocument,
    write_chunk_artifacts,
)

SOURCE_CHECKSUM = "a" * 64
PROVENANCE = ExtractionProvenance(
    parser_name="fake-pdf",
    parser_version="1.0.0",
    source_checksum=SOURCE_CHECKSUM,
)


def _page(
    page_number: int,
    text: str,
    *,
    section_title: str | None = None,
    provenance: ExtractionProvenance = PROVENANCE,
) -> PublicationPage:
    return PublicationPage(
        page_number=page_number,
        text=text,
        section_title=section_title,
        provenance=provenance,
    )


def _publication() -> ResearchPublication:
    return ResearchPublication(
        publication_id="pub:kida:page-aware",
        publication_type=PublicationType.DEFENSE_FORUM,
        title="유무인복합체계 정책 연구",
    )


def test_chunker_preserves_multi_page_provenance_and_exact_text() -> None:
    pages = [
        _page(12, "서론...", section_title="정책 분석"),
        _page(13, "유무인복합체계...", section_title="정책 분석"),
    ]
    chunker = DeterministicPageChunker(max_characters=1_000)

    chunks = chunker.chunk(_publication(), pages)

    assert isinstance(chunker, PublicationChunker)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.publication_id == "pub:kida:page-aware"
    assert chunk.page_start == 12
    assert chunk.page_end == 13
    assert chunk.chunk_index == 0
    assert chunk.section_title == "정책 분석"
    assert chunk.text == "서론...\n\n유무인복합체계..."
    assert chunk.checksum == sha256(chunk.text.encode("utf-8")).hexdigest()
    assert chunk.provenance == PROVENANCE
    assert [span.model_dump() for span in chunk.page_spans] == [
        {"page_number": 12, "start_offset": 0, "end_offset": len("서론...\n\n")},
        {
            "page_number": 13,
            "start_offset": len("서론...\n\n"),
            "end_offset": len(chunk.text),
        },
    ]


def test_every_character_offset_maps_to_exactly_one_page_with_blank_lines() -> None:
    first_page_text = "첫 문단\n\n같은 페이지의 둘째 문단"
    second_page_text = "둘째 페이지 본문"
    chunk = DeterministicPageChunker(max_characters=1_000).chunk(
        _publication(),
        [_page(12, first_page_text), _page(13, second_page_text)],
    )[0]

    offsets_by_page = {
        page_number: [
            offset
            for offset in range(len(chunk.text))
            if any(
                span.page_number == page_number and span.start_offset <= offset < span.end_offset
                for span in chunk.page_spans
            )
        ]
        for page_number in (12, 13)
    }
    matches_per_offset = [
        [
            span.page_number
            for span in chunk.page_spans
            if span.start_offset <= offset < span.end_offset
        ]
        for offset in range(len(chunk.text))
    ]

    assert all(len(matches) == 1 for matches in matches_per_offset)
    assert all(matches == [12] for matches in matches_per_offset[: len(first_page_text) + 2])
    assert all(matches == [13] for matches in matches_per_offset[len(first_page_text) + 2 :])
    assert offsets_by_page[12] == list(range(0, len(first_page_text) + 2))
    assert offsets_by_page[13] == list(range(len(first_page_text) + 2, len(chunk.text)))


def test_same_input_is_deterministic_and_text_change_changes_identity() -> None:
    publication = _publication()
    pages = [_page(1, "KAMD와 킬체인")]
    chunker = DeterministicPageChunker(max_characters=100)

    first = chunker.chunk(publication, pages)
    second = chunker.chunk(publication, pages)
    changed = chunker.chunk(
        publication,
        [_page(1, "KAMD와 한국형3축체계")],
    )
    versioned = DeterministicPageChunker(
        max_characters=100,
        chunking_version="page-window-v2",
    ).chunk(publication, pages)

    assert [chunk.model_dump_json() for chunk in first] == [
        chunk.model_dump_json() for chunk in second
    ]
    assert first[0].chunk_id.encode("utf-8") == second[0].chunk_id.encode("utf-8")
    assert first[0].checksum.encode("utf-8") == second[0].checksum.encode("utf-8")
    assert first[0].chunk_id != changed[0].chunk_id
    assert first[0].checksum != changed[0].checksum
    assert first[0].chunk_id != versioned[0].chunk_id
    assert first[0].checksum == versioned[0].checksum


def test_parser_version_change_changes_lineage_identity_only() -> None:
    publication = _publication()
    original = DeterministicPageChunker(max_characters=100).chunk(
        publication,
        [_page(1, "동일한 추출 본문")],
    )[0]
    changed_provenance = PROVENANCE.model_copy(update={"parser_version": "2.0.0"})
    reextracted = DeterministicPageChunker(max_characters=100).chunk(
        publication,
        [_page(1, "동일한 추출 본문", provenance=changed_provenance)],
    )[0]

    assert original.provenance.parser_version == "1.0.0"
    assert reextracted.provenance.parser_version == "2.0.0"
    assert original.provenance.parser_name == reextracted.provenance.parser_name
    assert original.provenance.source_checksum == reextracted.provenance.source_checksum
    assert original.chunk_id != reextracted.chunk_id
    assert original.checksum == reextracted.checksum
    assert original.model_dump(exclude={"chunk_id", "provenance"}) == reextracted.model_dump(
        exclude={"chunk_id", "provenance"}
    )


def test_provenance_change_creates_a_chunk_boundary() -> None:
    fallback_provenance = ExtractionProvenance(
        parser_name="fake-ocr",
        parser_version="3.1.0",
        source_checksum=SOURCE_CHECKSUM,
    )

    chunks = DeterministicPageChunker(max_characters=1_000).chunk(
        _publication(),
        [
            _page(1, "기본 추출 페이지"),
            _page(2, "OCR 대체 페이지", provenance=fallback_provenance),
        ],
    )

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 1), (2, 2)]
    assert [chunk.provenance for chunk in chunks] == [PROVENANCE, fallback_provenance]


def test_same_provenance_consecutive_pages_still_merge() -> None:
    chunks = DeterministicPageChunker(max_characters=1_000).chunk(
        _publication(),
        [_page(1, "첫 페이지"), _page(2, "둘째 페이지")],
    )

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 2)]
    assert chunks[0].provenance == PROVENANCE


def test_blank_page_and_section_change_create_provenance_boundaries() -> None:
    pages = [
        _page(1, "첫 페이지", section_title="서론"),
        _page(2, "  \n", section_title="서론"),
        _page(3, "셋째 페이지", section_title="본론"),
        _page(4, "넷째 페이지", section_title="결론"),
    ]

    chunks = DeterministicPageChunker(max_characters=1_000).chunk(_publication(), pages)

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [
        (1, 1),
        (3, 3),
        (4, 4),
    ]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]


def test_character_limit_never_splits_a_source_page() -> None:
    pages = [
        _page(1, "가" * 4),
        _page(2, "나" * 4),
        _page(3, "다" * 20),
    ]

    chunks = DeterministicPageChunker(max_characters=10).chunk(_publication(), pages)

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 2), (3, 3)]
    assert chunks[0].text == f"{'가' * 4}\n\n{'나' * 4}"
    assert chunks[1].text == "다" * 20


def test_character_limit_boundary_fires_before_adding_the_next_page() -> None:
    chunks = DeterministicPageChunker(max_characters=9).chunk(
        _publication(),
        [_page(1, "가" * 4), _page(2, "나" * 4)],
    )

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 1), (2, 2)]


def test_no_overlap_keeps_pages_in_exactly_one_chunk() -> None:
    chunker = DeterministicPageChunker(max_characters=9)

    chunks = chunker.chunk(
        _publication(),
        [_page(1, "가" * 4), _page(2, "나" * 4), _page(3, "다" * 4)],
    )

    assert chunker.settings.overlap_unit == "none"
    assert chunker.settings.overlap_size == 0
    assert [span.page_number for chunk in chunks for span in chunk.page_spans] == [1, 2, 3]
    assert all(
        chunk.page_spans[0].start_offset == 0 and chunk.page_spans[-1].end_offset == len(chunk.text)
        for chunk in chunks
    )


def test_structural_looking_text_is_preserved_without_synthetic_boundaries() -> None:
    pages = [
        _page(1, "본문\n표 1. 전력 | 수량\n전차 | 10\n1) 표와 같은 페이지의 각주"),
        _page(2, "참고문헌\n[1] 국방정책연구, 2025."),
    ]

    chunks = DeterministicPageChunker(max_characters=1_000).chunk(_publication(), pages)

    assert len(chunks) == 1
    assert chunks[0].text == f"{pages[0].text}\n\n{pages[1].text}"
    assert [span.page_number for span in chunks[0].page_spans] == [1, 2]


def test_ordinary_text_uses_the_same_page_preserving_policy() -> None:
    pages = [_page(1, "일반 본문 첫 페이지"), _page(2, "일반 본문 둘째 페이지")]

    chunks = DeterministicPageChunker(max_characters=1_000).chunk(_publication(), pages)

    assert len(chunks) == 1
    assert chunks[0].text == "일반 본문 첫 페이지\n\n일반 본문 둘째 페이지"
    assert chunks[0].metadata == {}


def test_nonconsecutive_pages_create_a_page_gap_boundary() -> None:
    chunks = DeterministicPageChunker(max_characters=1_000).chunk(
        _publication(),
        [
            _page(1, "첫 페이지", section_title="동일 절"),
            _page(3, "셋째 페이지", section_title="동일 절"),
        ],
    )

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 1), (3, 3)]


def test_empty_page_sequence_returns_no_chunks() -> None:
    assert DeterministicPageChunker().chunk(_publication(), []) == []


@pytest.mark.parametrize(
    "pages",
    [
        [_page(2, "둘"), _page(1, "하나")],
        [_page(1, "하나"), _page(1, "중복")],
    ],
)
def test_chunker_rejects_out_of_order_or_duplicate_pages(
    pages: list[PublicationPage],
) -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        DeterministicPageChunker().chunk(_publication(), pages)


@pytest.mark.parametrize("max_characters", [0, -1, True])
def test_chunker_rejects_invalid_character_limits(max_characters: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        DeterministicPageChunker(max_characters=max_characters)


def test_chunker_rejects_blank_version() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        DeterministicPageChunker(chunking_version="  ")


def test_chunk_artifacts_are_canonical_and_byte_reproducible(tmp_path: Path) -> None:
    second_provenance = ExtractionProvenance(
        parser_name="fixture-json",
        parser_version="2.0.0",
        source_checksum="b" * 64,
    )
    first_document = ChunkingDocument(
        ResearchPublication(
            publication_id="pub:kida:a",
            publication_type=PublicationType.KIDA_BRIEF,
            title="첫 문서",
        ),
        [_page(1, "첫 문서 1쪽"), _page(2, "첫 문서 2쪽")],
    )
    second_document = ChunkingDocument(
        ResearchPublication(
            publication_id="pub:kida:b",
            publication_type=PublicationType.RESEARCH_REPORT,
            title="둘째 문서",
        ),
        [_page(1, "둘째 문서", provenance=second_provenance)],
    )
    first_output = tmp_path / "first" / "artifacts" / "corpus"
    second_output = tmp_path / "second" / "artifacts" / "corpus"
    chunker = DeterministicPageChunker(max_characters=1_000)

    first_manifest = write_chunk_artifacts(
        [second_document, first_document],
        first_output,
        chunker=chunker,
    )
    second_manifest = write_chunk_artifacts(
        [first_document, second_document],
        second_output,
        chunker=DeterministicPageChunker(max_characters=1_000),
    )

    first_chunks = (first_output / CHUNKS_FILENAME).read_bytes()
    second_chunks = (second_output / CHUNKS_FILENAME).read_bytes()
    first_manifest_bytes = (first_output / CHUNK_MANIFEST_FILENAME).read_bytes()
    second_manifest_bytes = (second_output / CHUNK_MANIFEST_FILENAME).read_bytes()
    assert first_chunks == second_chunks
    assert first_manifest_bytes == second_manifest_bytes
    assert first_chunks.endswith(b"\n")
    assert first_manifest_bytes.endswith(b"\n")
    assert [json.loads(line)["publication_id"] for line in first_chunks.splitlines()] == [
        "pub:kida:a",
        "pub:kida:b",
    ]
    assert first_manifest == second_manifest
    assert ChunkArtifactManifest.model_validate_json(first_manifest_bytes) == first_manifest
    assert first_manifest.manifest_version == CHUNK_MANIFEST_VERSION
    assert first_manifest.input_document_count == 2
    assert first_manifest.input_page_count == 3
    assert first_manifest.chunk_count == 2
    assert first_manifest.chunks_sha256 == sha256(first_chunks).hexdigest()
    assert first_manifest.chunks_size_bytes == len(first_chunks)
    assert first_manifest.settings == chunker.settings
    assert [entry.model_dump() for entry in first_manifest.parser_provenance_distribution] == [
        {
            "parser_name": "fake-pdf",
            "parser_version": "1.0.0",
            "document_count": 1,
            "page_count": 2,
            "chunk_count": 1,
        },
        {
            "parser_name": "fixture-json",
            "parser_version": "2.0.0",
            "document_count": 1,
            "page_count": 1,
            "chunk_count": 1,
        },
    ]


def test_chunk_artifacts_reject_duplicate_publications_before_writing(tmp_path: Path) -> None:
    document = ChunkingDocument(_publication(), [_page(1, "본문")])
    output = tmp_path / "artifacts" / "corpus"

    with pytest.raises(ValueError, match="publication_id must be unique"):
        write_chunk_artifacts([document, document], output)

    assert not output.exists()


def test_chunk_artifacts_reject_output_outside_artifacts_corpus(tmp_path: Path) -> None:
    document = ChunkingDocument(_publication(), [_page(1, "본문")])

    with pytest.raises(ValueError, match="artifacts/corpus"):
        write_chunk_artifacts([document], tmp_path / "data" / "corpus")

    assert not (tmp_path / "data").exists()
