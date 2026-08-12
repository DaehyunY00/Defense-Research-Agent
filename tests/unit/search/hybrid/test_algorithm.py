"""Contract tests for deterministic publication-level rank fusion."""

import math
from collections.abc import Collection

import pytest

from defense_research_agent.domain import SearchField
from defense_research_agent.search.base import SearchMatch
from defense_research_agent.search.hybrid import (
    DEFAULT_RRF_K,
    HYBRID_FILTER_STAGE,
    HYBRID_TIE_BREAKER,
    RRF_FUSION_STRATEGY,
    RRF_FUSION_VERSION,
    HybridFailureCode,
    HybridSearchAlgorithm,
    HybridSearchStatus,
    HybridVectorCoverageStatus,
    HybridVectorStatus,
)
from defense_research_agent.search.vector import VectorSearchAlgorithm

from ._support import CHUNKING_VERSION, StaticEmbeddingProvider, make_chunk


class _StaticLexicalSearch:
    def __init__(self, matches: list[SearchMatch], *, fail: bool = False) -> None:
        self._matches = matches
        self._fail = fail
        self.allowed_calls: list[tuple[str, ...] | None] = []

    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[SearchMatch]:
        del query
        allowed = (
            None if allowed_publication_ids is None else tuple(sorted(allowed_publication_ids))
        )
        self.allowed_calls.append(allowed)
        if self._fail:
            raise RuntimeError("lexical baseline unavailable")
        allowed_set = None if allowed is None else set(allowed)
        return [
            match
            for match in self._matches
            if allowed_set is None or match.publication_id in allowed_set
        ][:limit]


def _lexical_match(publication_id: str, score: float) -> SearchMatch:
    return SearchMatch(
        publication_id=publication_id,
        score=score,
        matched_fields=(SearchField.TITLE,),
        matched_terms=("방위",),
    )


def _vector_algorithm(
    ranked_chunks: list[tuple[str, int, str, float]],
    *,
    fail_query: bool = False,
) -> VectorSearchAlgorithm:
    vectors: dict[str, tuple[float, float]] = {"query": (1.0, 0.0)}
    chunks = []
    for publication_id, chunk_index, text, cosine in ranked_chunks:
        vectors[text] = (cosine, math.sqrt(1.0 - cosine * cosine))
        chunks.append(make_chunk(publication_id, chunk_index, text))
    algorithm = VectorSearchAlgorithm(
        StaticEmbeddingProvider(vectors, fail_query=fail_query),
        chunking_version=CHUNKING_VERSION,
    )
    algorithm.build_index(chunks)
    return algorithm


def test_cross_source_ranks_are_fused_without_mixing_raw_scores() -> None:
    lexical = _StaticLexicalSearch(
        [
            _lexical_match("pub:a", 901.25),
            _lexical_match("pub:b", 17.0),
            _lexical_match("pub:c", 3.5),
        ]
    )
    vector = _vector_algorithm(
        [
            ("pub:b", 0, "bravo", 0.9),
            ("pub:c", 0, "charlie", 0.8),
            ("pub:a", 0, "alpha", 0.7),
        ]
    )

    result = HybridSearchAlgorithm(lexical, vector).search("query", None, 3)

    assert [match.publication_id for match in result.matches] == ["pub:b", "pub:a", "pub:c"]
    assert result.matches[0].lexical_score == 17.0
    assert result.matches[0].vector_score == pytest.approx(0.9)
    assert (result.matches[0].lexical_rank, result.matches[0].vector_rank) == (2, 1)
    assert result.matches[0].fusion_score == math.fsum(
        [1 / (DEFAULT_RRF_K + 2), 1 / (DEFAULT_RRF_K + 1)]
    )
    assert result.fusion.strategy == RRF_FUSION_STRATEGY
    assert result.fusion.version == RRF_FUSION_VERSION
    assert result.fusion.rrf_k == DEFAULT_RRF_K
    assert result.fusion.tie_breaker == HYBRID_TIE_BREAKER


def test_source_only_matches_are_kept_in_both_directions() -> None:
    lexical = _StaticLexicalSearch(
        [
            _lexical_match("pub:lexical-only", 800.0),
            _lexical_match("pub:shared", 2.0),
        ]
    )
    vector = _vector_algorithm(
        [
            ("pub:shared", 0, "shared", 0.95),
            ("pub:vector-only", 0, "vector", 0.75),
        ]
    )

    result = HybridSearchAlgorithm(lexical, vector).search("query", None, 10)
    by_id = {match.publication_id: match for match in result.matches}

    assert set(by_id) == {"pub:lexical-only", "pub:shared", "pub:vector-only"}
    assert by_id["pub:lexical-only"].lexical_score == 800.0
    assert by_id["pub:lexical-only"].vector_score is None
    assert by_id["pub:lexical-only"].vector_rank is None
    assert by_id["pub:lexical-only"].vector_chunk is None
    assert by_id["pub:vector-only"].lexical_score is None
    assert by_id["pub:vector-only"].lexical_rank is None
    assert by_id["pub:vector-only"].vector_score == pytest.approx(0.75)
    assert by_id["pub:vector-only"].vector_chunk is not None


def test_vector_chunks_fold_to_best_chunk_and_contiguous_publication_rank() -> None:
    vector = _vector_algorithm(
        [
            ("pub:a", 0, "a-best", 0.99),
            ("pub:a", 1, "a-worse", 0.95),
            ("pub:b", 0, "b", 0.8),
        ]
    )

    result = HybridSearchAlgorithm(_StaticLexicalSearch([]), vector).search("query", None, 10)
    by_id = {match.publication_id: match for match in result.matches}

    assert result.vector_chunk_candidate_count == 3
    assert result.vector_publication_candidate_count == 2
    assert by_id["pub:a"].vector_rank == 1
    assert by_id["pub:a"].vector_chunk_rank == 1
    assert by_id["pub:a"].vector_chunk is not None
    assert by_id["pub:a"].vector_chunk.text == "a-best"
    assert by_id["pub:b"].vector_rank == 2
    assert by_id["pub:b"].vector_chunk_rank == 3


def test_unbuilt_vector_index_returns_visible_lexical_only_fallback() -> None:
    lexical = _StaticLexicalSearch([_lexical_match("pub:a", 10.0)])
    vector = VectorSearchAlgorithm(
        StaticEmbeddingProvider({"query": (1.0, 0.0)}),
        chunking_version=CHUNKING_VERSION,
    )

    result = HybridSearchAlgorithm(lexical, vector).search("query", None, 10)

    assert result.status is HybridSearchStatus.LEXICAL_ONLY_FALLBACK
    assert result.vector_status is HybridVectorStatus.INDEX_NOT_BUILT
    assert result.failure is not None
    assert result.failure.code is HybridFailureCode.VECTOR_INDEX_NOT_BUILT
    assert [match.publication_id for match in result.matches] == ["pub:a"]
    assert result.matches[0].fusion_score == 1 / (DEFAULT_RRF_K + 1)
    assert result.matches[0].vector_score is None
    assert result.matches[0].vector_coverage_status is HybridVectorCoverageStatus.UNAVAILABLE


def test_vector_query_failure_returns_visible_lexical_only_fallback() -> None:
    lexical = _StaticLexicalSearch([_lexical_match("pub:a", 10.0)])
    vector = _vector_algorithm(
        [("pub:a", 0, "alpha", 0.9)],
        fail_query=True,
    )

    result = HybridSearchAlgorithm(lexical, vector).search("query", None, 10)

    assert result.status is HybridSearchStatus.LEXICAL_ONLY_FALLBACK
    assert result.vector_status is HybridVectorStatus.SEARCH_FAILED
    assert result.failure is not None
    assert result.failure.code is HybridFailureCode.VECTOR_SEARCH_FAILED
    assert "simulated" not in result.failure.message
    assert result.vector_index is not None
    assert result.vector_chunk_candidate_count == 0


def test_filter_is_applied_to_each_source_before_rank_fusion() -> None:
    lexical = _StaticLexicalSearch(
        [
            _lexical_match("pub:excluded", 100.0),
            _lexical_match("pub:a", 10.0),
            _lexical_match("pub:b", 5.0),
        ]
    )
    vector = _vector_algorithm(
        [
            ("pub:excluded", 0, "excluded", 0.99),
            ("pub:b", 0, "bravo", 0.9),
            ("pub:a", 0, "alpha", 0.8),
        ]
    )

    result = HybridSearchAlgorithm(lexical, vector).search("query", {"pub:b", "pub:a"}, 10)
    by_id = {match.publication_id: match for match in result.matches}

    assert lexical.allowed_calls == [("pub:a", "pub:b")]
    assert result.fusion.filter_stage == HYBRID_FILTER_STAGE
    assert set(by_id) == {"pub:a", "pub:b"}
    assert by_id["pub:a"].lexical_rank == 1
    assert by_id["pub:b"].vector_rank == 1
    assert all(match.publication_id != "pub:excluded" for match in result.matches)


def test_equal_fusion_score_uses_publication_id_tie_breaker_and_limit_truncates() -> None:
    lexical = _StaticLexicalSearch([_lexical_match("pub:b", 999.0)])
    vector = _vector_algorithm([("pub:a", 0, "alpha", 0.6)])

    result = HybridSearchAlgorithm(lexical, vector).search("query", None, 1)

    assert result.fusion_candidate_count == 2
    assert len(result.matches) == 1
    assert result.matches[0].publication_id == "pub:a"
    assert result.matches[0].fusion_score == 1 / (DEFAULT_RRF_K + 1)


def test_source_depth_truncation_is_visible_and_changes_rrf_boundary() -> None:
    lexical = _StaticLexicalSearch(
        [
            _lexical_match("pub:a", 40.0),
            _lexical_match("pub:b", 30.0),
            _lexical_match("pub:c", 20.0),
            _lexical_match("pub:cut-both", 10.0),
        ]
    )
    vector = _vector_algorithm(
        [
            ("pub:b", 0, "bravo", 0.95),
            ("pub:c", 0, "charlie", 0.90),
            ("pub:vector-boundary", 0, "vector-boundary", 0.85),
            ("pub:a", 0, "alpha", 0.80),
            ("pub:cut-both", 0, "cut-both", 0.75),
        ]
    )
    algorithm = HybridSearchAlgorithm(
        lexical,
        vector,
        candidate_limit_per_source=3,
    )

    result = algorithm.search("query", None, 3)
    by_id = {match.publication_id: match for match in result.matches}

    assert result.lexical_candidate_count == 3
    assert result.lexical_candidates_truncated is True
    assert result.vector_publication_candidate_count == 3
    assert result.vector_publication_candidates_truncated is True
    assert result.vector_chunk_candidate_count == 5
    assert result.fusion_candidate_count == 4
    assert [match.publication_id for match in result.matches] == ["pub:b", "pub:c", "pub:a"]
    assert by_id["pub:c"].lexical_rank == 3
    assert by_id["pub:a"].vector_rank is None
    assert by_id["pub:a"].vector_coverage_status is (
        HybridVectorCoverageStatus.SOURCE_DEPTH_TRUNCATED
    )
    assert by_id["pub:a"].vector_observed_publication_rank == 4
    assert by_id["pub:a"].fusion_score == 1 / (DEFAULT_RRF_K + 1)
    assert "pub:cut-both" not in result.model_dump_json()


def test_exact_source_depth_is_not_misreported_as_truncated() -> None:
    lexical = _StaticLexicalSearch(
        [
            _lexical_match("pub:a", 30.0),
            _lexical_match("pub:b", 20.0),
            _lexical_match("pub:c", 10.0),
        ]
    )
    vector = _vector_algorithm(
        [
            ("pub:c", 0, "charlie", 0.9),
            ("pub:b", 0, "bravo", 0.8),
            ("pub:a", 0, "alpha", 0.7),
        ]
    )

    result = HybridSearchAlgorithm(
        lexical,
        vector,
        candidate_limit_per_source=3,
    ).search("query", None, 3)

    assert result.lexical_candidate_count == 3
    assert result.lexical_candidates_truncated is False
    assert result.vector_publication_candidate_count == 3
    assert result.vector_publication_candidates_truncated is False


def test_same_input_and_fusion_version_produce_byte_identical_result() -> None:
    lexical = _StaticLexicalSearch([_lexical_match("pub:a", 123.0), _lexical_match("pub:b", 4.5)])
    vector = _vector_algorithm([("pub:b", 0, "bravo", 0.9), ("pub:a", 0, "alpha", 0.8)])
    algorithm = HybridSearchAlgorithm(
        lexical,
        vector,
        rrf_k=23,
        candidate_limit_per_source=7,
    )

    first = algorithm.search("query", {"pub:b", "pub:a"}, 1)
    second = algorithm.search("query", {"pub:a", "pub:b"}, 1)

    assert first.fusion.version == second.fusion.version == RRF_FUSION_VERSION
    assert first.model_dump_json().encode("utf-8") == second.model_dump_json().encode("utf-8")


def test_short_circuit_boundaries_do_not_call_sources() -> None:
    lexical = _StaticLexicalSearch([_lexical_match("pub:a", 1.0)])
    vector = _vector_algorithm([("pub:a", 0, "alpha", 0.8)])
    algorithm = HybridSearchAlgorithm(lexical, vector)

    zero_limit = algorithm.search("query", None, 0)
    blank_query = algorithm.search("   ", None, 10)
    empty_filter = algorithm.search("query", set(), 10)

    assert lexical.allowed_calls == []
    assert zero_limit.status is HybridSearchStatus.NOT_RUN
    assert blank_query.status is HybridSearchStatus.NOT_RUN
    assert empty_filter.status is HybridSearchStatus.NOT_RUN
    assert zero_limit.matches == blank_query.matches == empty_filter.matches == []


def test_lexical_failure_is_not_misreported_as_vector_only_success() -> None:
    lexical = _StaticLexicalSearch([], fail=True)
    vector = _vector_algorithm([("pub:a", 0, "alpha", 0.8)])

    with pytest.raises(RuntimeError, match="lexical baseline unavailable"):
        HybridSearchAlgorithm(lexical, vector).search("query", None, 10)


@pytest.mark.parametrize("field_name", ["rrf_k", "candidate_limit_per_source"])
@pytest.mark.parametrize("invalid_value", [True, 0, -1])
def test_constructor_rejects_invalid_positive_integer_parameters(
    field_name: str,
    invalid_value: int,
) -> None:
    lexical = _StaticLexicalSearch([])
    vector = _vector_algorithm([])
    arguments = {field_name: invalid_value}

    with pytest.raises(ValueError, match="positive integer"):
        HybridSearchAlgorithm(lexical, vector, **arguments)
