"""Tests for the dependency-free lexical ranking algorithm."""

from defense_research_agent.domain import PublicationType, ResearchPublication, SearchField
from defense_research_agent.search import LocalLexicalSearchAlgorithm


def test_title_weight_outranks_same_single_content_match() -> None:
    publications = [
        ResearchPublication(
            publication_id="pub:content",
            publication_type=PublicationType.RESEARCH_REPORT,
            title="일반 보고서",
            content="인공지능 기반 정책",
        ),
        ResearchPublication(
            publication_id="pub:title",
            publication_type=PublicationType.DEFENSE_FORUM,
            title="인공지능 정책",
            content="일반 본문",
        ),
    ]
    algorithm = LocalLexicalSearchAlgorithm()
    algorithm.build_index(publications)

    results = algorithm.search("인공지능", None, 10)

    assert [result.publication_id for result in results] == [
        "pub:title",
        "pub:content",
    ]
    assert results[0].matched_fields == (SearchField.TITLE,)


def test_algorithm_handles_empty_query_candidates_and_limit() -> None:
    publication = ResearchPublication(
        publication_id="pub:test",
        publication_type=PublicationType.KIDA_BRIEF,
        title="국방 정책",
    )
    algorithm = LocalLexicalSearchAlgorithm()
    algorithm.build_index([publication])

    assert algorithm.search("", None, 10) == []
    assert algorithm.search("국방", set(), 10) == []
    assert algorithm.search("국방", None, 0) == []
