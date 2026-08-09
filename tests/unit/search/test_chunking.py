"""Tests for deterministic page-aware publication chunking."""

from hashlib import sha256

import pytest

from defense_research_agent.domain import PublicationPage, PublicationType, ResearchPublication
from defense_research_agent.search import DeterministicPageChunker, PublicationChunker


def _publication() -> ResearchPublication:
    return ResearchPublication(
        publication_id="pub:kida:page-aware",
        publication_type=PublicationType.DEFENSE_FORUM,
        title="유무인복합체계 정책 연구",
    )


def test_chunker_preserves_multi_page_provenance_and_exact_text() -> None:
    pages = [
        PublicationPage(page_number=12, section_title="정책 분석", text="서론..."),
        PublicationPage(
            page_number=13,
            section_title="정책 분석",
            text="유무인복합체계...",
        ),
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


def test_same_input_is_deterministic_and_text_change_changes_identity() -> None:
    publication = _publication()
    pages = [PublicationPage(page_number=1, text="KAMD와 킬체인")]
    chunker = DeterministicPageChunker(max_characters=100)

    first = chunker.chunk(publication, pages)
    second = chunker.chunk(publication, pages)
    changed = chunker.chunk(
        publication,
        [PublicationPage(page_number=1, text="KAMD와 한국형3축체계")],
    )
    versioned = DeterministicPageChunker(
        max_characters=100,
        chunking_version="page-window-v2",
    ).chunk(publication, pages)

    assert [chunk.model_dump_json() for chunk in first] == [
        chunk.model_dump_json() for chunk in second
    ]
    assert first[0].chunk_id != changed[0].chunk_id
    assert first[0].checksum != changed[0].checksum
    assert first[0].chunk_id != versioned[0].chunk_id
    assert first[0].checksum == versioned[0].checksum


def test_blank_page_and_section_change_create_provenance_boundaries() -> None:
    pages = [
        PublicationPage(page_number=1, section_title="서론", text="첫 페이지"),
        PublicationPage(page_number=2, section_title="서론", text="  \n"),
        PublicationPage(page_number=3, section_title="본론", text="셋째 페이지"),
        PublicationPage(page_number=4, section_title="결론", text="넷째 페이지"),
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
        PublicationPage(page_number=1, text="가" * 4),
        PublicationPage(page_number=2, text="나" * 4),
        PublicationPage(page_number=3, text="다" * 20),
    ]

    chunks = DeterministicPageChunker(max_characters=10).chunk(_publication(), pages)

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 2), (3, 3)]
    assert chunks[0].text == f"{'가' * 4}\n\n{'나' * 4}"
    assert chunks[1].text == "다" * 20


def test_empty_page_sequence_returns_no_chunks() -> None:
    assert DeterministicPageChunker().chunk(_publication(), []) == []


@pytest.mark.parametrize(
    "pages",
    [
        [PublicationPage(page_number=2, text="둘"), PublicationPage(page_number=1, text="하나")],
        [PublicationPage(page_number=1, text="하나"), PublicationPage(page_number=1, text="중복")],
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
