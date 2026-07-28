"""Tests for the in-memory research publication repository."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import (
    JsonObject,
    PublicationSearchFilters,
    PublicationType,
    ResearchPublication,
    SearchField,
)
from defense_research_agent.repositories import (
    InMemoryResearchPublicationRepository,
    ResearchPublicationRepository,
)


def _publication(
    publication_id: str,
    *,
    publication_type: PublicationType = PublicationType.DEFENSE_FORUM,
    title: str | None = None,
    abstract: str | None = None,
    keywords: list[str] | None = None,
    content: str | None = None,
    authors: list[str] | None = None,
    publication_date: date | None = None,
    filename_year: int | None = None,
) -> ResearchPublication:
    raw_metadata: JsonObject = {}
    if filename_year is not None:
        raw_metadata["_ingestion"] = {"filename_year": filename_year}
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=publication_type,
        title=title,
        abstract=abstract,
        keywords=keywords or [],
        content=content,
        authors=authors or [],
        publication_date=publication_date,
        raw_metadata=raw_metadata,
    )


@pytest.fixture
def repository() -> ResearchPublicationRepository:
    """Repository with search terms isolated to each supported field."""
    return InMemoryResearchPublicationRepository(
        [
            _publication(
                "pub:title",
                title="미래 국방 인공지능 전략",
                content="일반적인 배경 설명",
                authors=["김제목"],
                publication_date=date(2025, 1, 15),
            ),
            _publication(
                "pub:abstract",
                title="군수 분야 연구",
                abstract="군수혁신분석을 위한 정책 프레임워크",
                authors=["이초록"],
                publication_date=date(2024, 6, 1),
            ),
            _publication(
                "pub:keywords",
                publication_type=PublicationType.KIDA_BRIEF,
                title="우주 분야 브리프",
                keywords=["우주안보", "위성"],
                authors=["박키워드"],
                publication_date=date(2023, 3, 1),
            ),
            _publication(
                "pub:content",
                publication_type=PublicationType.RESEARCH_REPORT,
                title="국방 인력 연구",
                content="동원정책과 인력구조를 한글 본문에서 분석한다.",
                authors=["최본문"],
                filename_year=2022,
            ),
        ]
    )


@pytest.mark.parametrize(
    ("query", "expected_id", "expected_field"),
    [
        ("인공지능", "pub:title", SearchField.TITLE),
        ("군수혁신분석", "pub:abstract", SearchField.ABSTRACT),
        ("우주안보", "pub:keywords", SearchField.KEYWORDS),
        ("동원정책", "pub:content", SearchField.CONTENT),
    ],
)
def test_searches_each_supported_field(
    repository: ResearchPublicationRepository,
    query: str,
    expected_id: str,
    expected_field: SearchField,
) -> None:
    results = repository.search(query, limit=10)

    assert results[0].publication.publication_id == expected_id
    assert expected_field in results[0].matched_fields
    assert query.casefold() in results[0].matched_terms
    assert results[0].score > 0


def test_filters_by_date_type_and_author(
    repository: ResearchPublicationRepository,
) -> None:
    filters = PublicationSearchFilters(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        publication_types=[PublicationType.DEFENSE_FORUM],
        authors=["김제목"],
    )

    results = repository.search("국방", filters, limit=10)

    assert [result.publication.publication_id for result in results] == ["pub:title"]


def test_date_filter_uses_filename_year_without_inventing_day(
    repository: ResearchPublicationRepository,
) -> None:
    filters = PublicationSearchFilters(
        start_date=date(2022, 7, 1),
        end_date=date(2022, 7, 31),
    )

    results = repository.search("동원정책", filters)

    assert [result.publication.publication_id for result in results] == ["pub:content"]
    assert results[0].publication.publication_date is None


def test_find_similar_and_related_keywords(
    repository: ResearchPublicationRepository,
) -> None:
    similar = repository.find_similar(
        "미래 국방 인공지능",
        "책임 있는 인공지능 활용",
        limit=2,
    )
    related = repository.find_related_by_keywords(["우주안보", "위성", "우주안보"], limit=2)

    assert similar[0].publication.publication_id == "pub:title"
    assert related[0].publication.publication_id == "pub:keywords"


def test_lookup_recent_and_distribution(
    repository: ResearchPublicationRepository,
) -> None:
    assert repository.get_by_id("pub:title") is not None
    assert repository.get_by_id("pub:missing") is None
    recent = repository.get_recent_publications(
        3,
        [PublicationType.DEFENSE_FORUM, PublicationType.RESEARCH_REPORT],
    )
    distribution = repository.get_publication_distribution(
        date(2022, 1, 1),
        date(2024, 12, 31),
    )

    assert [publication.publication_id for publication in recent] == [
        "pub:title",
        "pub:abstract",
        "pub:content",
    ]
    assert distribution.total == 3
    assert distribution.by_year == {2022: 1, 2023: 1, 2024: 1}
    assert distribution.by_publication_type == {
        "defense_forum": 1,
        "kida_brief": 1,
        "research_report": 1,
    }
    assert distribution.unknown_date_count == 0


def test_empty_inputs_and_no_results_are_safe() -> None:
    repository = InMemoryResearchPublicationRepository([])

    assert repository.search("국방") == []
    assert repository.search("") == []
    assert repository.search("국방", limit=0) == []
    assert repository.find_similar(None, None) == []
    assert repository.find_related_by_keywords([]) == []
    assert repository.get_recent_publications() == []
    assert repository.get_publication_distribution(None, None).total == 0


def test_identical_searches_have_stable_results(
    repository: ResearchPublicationRepository,
) -> None:
    first = repository.search("국방 인력 정책", limit=4)
    second = repository.search("국방 인력 정책", limit=4)

    assert [result.model_dump_json() for result in first] == [
        result.model_dump_json() for result in second
    ]


def test_invalid_date_ranges_are_rejected(
    repository: ResearchPublicationRepository,
) -> None:
    with pytest.raises(ValidationError, match="start_date"):
        PublicationSearchFilters(
            start_date=date(2025, 1, 1),
            end_date=date(2024, 1, 1),
        )
    with pytest.raises(ValueError, match="start_date"):
        repository.get_publication_distribution(
            date(2025, 1, 1),
            date(2024, 1, 1),
        )


def test_jsonl_loader_validates_each_publication(tmp_path: Path) -> None:
    valid_path = tmp_path / "publications.jsonl"
    publication = _publication("pub:jsonl", title="한글 JSONL")
    valid_path.write_text(
        f"\n{publication.model_dump_json()}\n",
        encoding="utf-8",
    )

    loaded = InMemoryResearchPublicationRepository.from_jsonl(valid_path)

    assert loaded.get_by_id("pub:jsonl") == publication

    invalid_path = tmp_path / "invalid.jsonl"
    invalid_path.write_text('{"publication_id":"broken"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        InMemoryResearchPublicationRepository.from_jsonl(invalid_path)


def test_constructor_rejects_duplicate_publication_ids() -> None:
    publications = [
        _publication("pub:duplicate", title="첫 번째 자료"),
        _publication("pub:duplicate", title="충돌하는 두 번째 자료"),
    ]

    with pytest.raises(ValueError, match="duplicate publication_id"):
        InMemoryResearchPublicationRepository(publications)
