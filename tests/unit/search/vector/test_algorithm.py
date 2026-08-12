"""Tests for deterministic, fail-closed chunk vector search."""

from collections.abc import Callable, Sequence

import pytest

from defense_research_agent.domain import (
    PublicationChunk,
    PublicationPageSpan,
    PublicationType,
    ResearchPublication,
    SearchField,
)
from defense_research_agent.search.base import PublicationSearchAlgorithm
from defense_research_agent.search.vector import (
    VECTOR_TIE_BREAKER,
    InMemoryVectorIndex,
    PublicationVectorSearchAdapter,
    VectorIndexNotBuiltError,
    VectorQueryEmbeddingError,
    VectorSearchAlgorithm,
    VectorSearchConfigurationError,
)

from ._support import (
    CHUNKING_VERSION,
    PROVENANCE,
    StaticEmbeddingProvider,
    make_chunk,
)


def test_equal_scores_use_declared_publication_chunk_tie_breaker() -> None:
    chunks = [
        make_chunk("pub:b", 0, "bravo"),
        make_chunk("pub:a", 1, "alpha one"),
        make_chunk("pub:a", 0, "alpha zero"),
    ]
    algorithm = VectorSearchAlgorithm(
        StaticEmbeddingProvider(default_vector=(1.0, 0.0)),
        chunking_version=CHUNKING_VERSION,
    )
    manifest = algorithm.build_index(chunks)

    results = algorithm.search("query", None, 10)

    assert [result.score for result in results] == [1.0, 1.0, 1.0]
    assert [(result.publication_id, result.chunk.chunk_index) for result in results] == [
        ("pub:a", 0),
        ("pub:a", 1),
        ("pub:b", 0),
    ]
    assert manifest.tie_breaker == VECTOR_TIE_BREAKER


def test_result_resolves_chunk_offset_to_original_page_provenance() -> None:
    first_text = "첫 페이지\n\n"
    second_text = "둘째 페이지의 근거"
    text = first_text + second_text
    chunk = make_chunk(
        "pub:trace",
        0,
        text,
        page_start=7,
        page_spans=[
            PublicationPageSpan(page_number=7, start_offset=0, end_offset=len(first_text)),
            PublicationPageSpan(
                page_number=8,
                start_offset=len(first_text),
                end_offset=len(text),
            ),
        ],
    )
    algorithm = VectorSearchAlgorithm(
        StaticEmbeddingProvider(),
        chunking_version=CHUNKING_VERSION,
    )
    algorithm.build_index([chunk])

    match = algorithm.search("query", None, 1)[0]

    assert match.publication_id == "pub:trace"
    assert match.chunk_id == chunk.chunk_id
    assert match.chunk.page_start == 7
    assert match.chunk.page_end == 8
    assert match.chunk.page_spans == chunk.page_spans
    assert match.chunk.provenance == PROVENANCE
    assert match.page_number_for_offset(text.index("근거")) == 8
    assert match.page_number_for_offset(len(first_text) - 1) == 7
    with pytest.raises(ValueError, match="within the chunk text"):
        match.page_number_for_offset(len(text))


def test_empty_index_empty_query_zero_limit_and_excess_limit_boundaries() -> None:
    provider = StaticEmbeddingProvider()
    empty_algorithm = VectorSearchAlgorithm(provider, chunking_version=CHUNKING_VERSION)
    empty_algorithm.build_index([])
    assert empty_algorithm.search("query", None, 10) == []

    chunks = [make_chunk("pub:a", 0, "alpha"), make_chunk("pub:b", 0, "bravo")]
    algorithm = VectorSearchAlgorithm(provider, chunking_version=CHUNKING_VERSION)
    algorithm.build_index(chunks)

    calls_before_short_circuits = provider.query_call_count
    assert algorithm.search("", None, 10) == []
    assert algorithm.search("   ", None, 10) == []
    assert algorithm.search("query", None, 0) == []
    assert algorithm.search("query", None, -1) == []
    assert algorithm.search("query", set(), 10) == []
    assert provider.query_call_count == calls_before_short_circuits
    assert len(algorithm.search("query", None, 100)) == 2


def test_allowed_publication_filter_applied_and_none_path_unfiltered() -> None:
    algorithm = VectorSearchAlgorithm(
        StaticEmbeddingProvider(),
        chunking_version=CHUNKING_VERSION,
    )
    algorithm.build_index([make_chunk("pub:a", 0, "alpha"), make_chunk("pub:b", 0, "bravo")])

    unfiltered = algorithm.search("query", None, 10)
    filtered = algorithm.search("query", {"pub:b", "pub:missing"}, 10)

    assert [match.publication_id for match in unfiltered] == ["pub:a", "pub:b"]
    assert [match.publication_id for match in filtered] == ["pub:b"]


def test_publication_projection_keeps_best_chunk_in_legacy_shape() -> None:
    vectors = {
        "query": (1.0, 0.0),
        "a-best": (1.0, 0.0),
        "a-worse": (0.0, 1.0),
        "b": (0.5, 0.5),
    }
    algorithm = VectorSearchAlgorithm(
        StaticEmbeddingProvider(vectors=vectors),
        chunking_version=CHUNKING_VERSION,
    )
    algorithm.build_index(
        [
            make_chunk("pub:a", 0, "a-best"),
            make_chunk("pub:a", 1, "a-worse"),
            make_chunk("pub:b", 0, "b"),
        ]
    )

    results = algorithm.search_publications("query", None, 10)

    assert [result.publication_id for result in results] == ["pub:a", "pub:b"]
    assert results[0].score == 1.0
    assert results[0].matched_fields == (SearchField.CONTENT,)
    assert results[0].matched_terms == ()


def test_legacy_abc_adapter_uses_explicit_chunk_factory() -> None:
    publications = [
        ResearchPublication(
            publication_id="pub:b",
            publication_type=PublicationType.DEFENSE_FORUM,
            title="두 번째",
        ),
        ResearchPublication(
            publication_id="pub:a",
            publication_type=PublicationType.RESEARCH_REPORT,
            title="첫 번째",
        ),
    ]

    def chunk_factory(
        publications: Sequence[ResearchPublication],
    ) -> Sequence[PublicationChunk]:
        return [
            make_chunk(publication.publication_id, 0, f"text for {publication.publication_id}")
            for publication in publications
        ]

    adapter = PublicationVectorSearchAdapter(
        VectorSearchAlgorithm(
            StaticEmbeddingProvider(),
            chunking_version=CHUNKING_VERSION,
        ),
        chunk_factory,
    )

    adapter.build_index(publications)
    results = adapter.search("query", None, 10)

    assert isinstance(adapter, PublicationSearchAlgorithm)
    assert [result.publication_id for result in results] == ["pub:a", "pub:b"]


def test_legacy_adapter_rejects_chunk_from_unsupplied_publication() -> None:
    publication = ResearchPublication(
        publication_id="pub:a",
        publication_type=PublicationType.KIDA_BRIEF,
    )

    def invalid_factory(
        publications: Sequence[ResearchPublication],
    ) -> Sequence[PublicationChunk]:
        assert publications == [publication]
        return [make_chunk("pub:outside", 0, "outside")]

    adapter = PublicationVectorSearchAdapter(
        VectorSearchAlgorithm(
            StaticEmbeddingProvider(),
            chunking_version=CHUNKING_VERSION,
        ),
        invalid_factory,
    )

    with pytest.raises(ValueError, match="outside the supplied publications"):
        adapter.build_index([publication])


ProviderFactory = Callable[[], StaticEmbeddingProvider]


@pytest.mark.parametrize(
    ("field_name", "provider_factory"),
    [
        (
            "embedding_model_id",
            lambda: StaticEmbeddingProvider(embedding_model_id="other-model"),
        ),
        (
            "embedding_version",
            lambda: StaticEmbeddingProvider(embedding_version="2.0.0"),
        ),
        (
            "dimension",
            lambda: StaticEmbeddingProvider(dimension=3, default_vector=(1.0, 0.0, 0.0)),
        ),
        (
            "normalization",
            lambda: StaticEmbeddingProvider(normalized=False),
        ),
    ],
    ids=["model-id", "model-version", "dimension", "normalization"],
)
def test_query_blocks_each_embedding_setting_mismatch_before_provider_call(
    field_name: str,
    provider_factory: ProviderFactory,
) -> None:
    index = InMemoryVectorIndex()
    index.build(
        [make_chunk("pub:a", 0, "alpha")],
        StaticEmbeddingProvider(),
        chunking_version=CHUNKING_VERSION,
    )
    query_provider = provider_factory()
    algorithm = VectorSearchAlgorithm(
        query_provider,
        chunking_version=CHUNKING_VERSION,
        index=index,
    )

    with pytest.raises(VectorSearchConfigurationError, match=field_name):
        algorithm.search("query", None, 1)

    assert query_provider.query_call_count == 0


def test_query_blocks_chunking_version_mismatch_before_provider_call() -> None:
    index = InMemoryVectorIndex()
    index.build(
        [make_chunk("pub:a", 0, "alpha")],
        StaticEmbeddingProvider(),
        chunking_version=CHUNKING_VERSION,
    )
    query_provider = StaticEmbeddingProvider()
    algorithm = VectorSearchAlgorithm(
        query_provider,
        chunking_version="other-chunks-v1",
        index=index,
    )

    with pytest.raises(VectorSearchConfigurationError, match="chunking_version"):
        algorithm.search("query", None, 1)

    assert query_provider.query_call_count == 0


def test_query_embedding_failure_is_not_silently_treated_as_no_results() -> None:
    provider = StaticEmbeddingProvider(fail_query=True)
    algorithm = VectorSearchAlgorithm(provider, chunking_version=CHUNKING_VERSION)
    algorithm.build_index([make_chunk("pub:a", 0, "alpha")])

    with pytest.raises(VectorQueryEmbeddingError, match="returned a failure"):
        algorithm.search("query", None, 1)


def test_nonempty_search_requires_a_built_index() -> None:
    algorithm = VectorSearchAlgorithm(
        StaticEmbeddingProvider(),
        chunking_version=CHUNKING_VERSION,
    )

    with pytest.raises(VectorIndexNotBuiltError, match="has not been built"):
        algorithm.search("query", None, 1)
