"""Tests for the deterministic, non-semantic fake embedding adapter."""

from hashlib import sha256
from math import sqrt

import pytest

from defense_research_agent.search.embeddings import FakeEmbeddingProvider
from defense_research_agent.search.embeddings.base import (
    EmbeddingBatchResult,
    EmbeddingErrorCode,
    EmbeddingProvider,
)


def _checksum(text: str) -> str:
    return sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def test_provider_implements_contract_and_exposes_configuration() -> None:
    provider = FakeEmbeddingProvider(
        dimension=12,
        normalized=True,
        max_batch_size=5,
        max_input_bytes=100,
    )

    assert isinstance(provider, EmbeddingProvider)
    assert provider.dimension == 12
    assert provider.normalized is True
    assert provider.max_batch_size == 5
    assert provider.max_input_bytes == 100


def test_same_input_and_settings_produce_byte_identical_results() -> None:
    first_provider = FakeEmbeddingProvider(
        dimension=9,
        normalized=True,
        max_batch_size=4,
        max_input_bytes=128,
    )
    second_provider = FakeEmbeddingProvider(
        dimension=9,
        normalized=True,
        max_batch_size=4,
        max_input_bytes=128,
    )
    texts = ["국방 정책 연구", "", "emoji: 🛰️"]

    first = first_provider.embed_documents(texts)
    second = second_provider.embed_documents(texts)

    assert first.model_dump_json() == second.model_dump_json()


def test_vector_values_do_not_depend_on_batch_order() -> None:
    provider = FakeEmbeddingProvider(dimension=9, normalized=False)

    first_order = provider.embed_documents(["target", "other"])
    second_order = provider.embed_documents(["other", "target"])

    assert first_order.vectors[0].input_checksum == second_order.vectors[1].input_checksum
    assert first_order.vectors[0].values == second_order.vectors[1].values


def test_dimension_and_normalization_settings_change_results() -> None:
    text = "configuration-sensitive fake vector"
    small = FakeEmbeddingProvider(dimension=3, normalized=False).embed_query(text)
    large = FakeEmbeddingProvider(dimension=7, normalized=False).embed_query(text)
    normalized = FakeEmbeddingProvider(dimension=3, normalized=True).embed_query(text)

    assert small.model_dump_json() != large.model_dump_json()
    assert small.model_dump_json() != normalized.model_dump_json()
    assert len(small.vectors[0].values) == 3
    assert len(large.vectors[0].values) == 7
    assert small.vectors[0].values != normalized.vectors[0].values


def test_normalized_vectors_have_unit_l2_norm() -> None:
    provider = FakeEmbeddingProvider(dimension=17, normalized=True)

    result = provider.embed_documents(["alpha", "한글", "👩🏽‍💻"])

    assert len(result.vectors) == 3
    for vector in result.vectors:
        norm = sqrt(sum(value * value for value in vector.values))
        assert norm == pytest.approx(1.0)


def test_one_bad_input_does_not_discard_other_batch_items() -> None:
    provider = FakeEmbeddingProvider(dimension=6, max_input_bytes=12)

    result = provider.embed_documents(["유효", "   ", "x" * 13, "🛰️"])

    assert [vector.input_index for vector in result.vectors] == [0, 3]
    assert [(failure.input_index, failure.code) for failure in result.failures] == [
        (1, EmbeddingErrorCode.EMPTY_INPUT),
        (2, EmbeddingErrorCode.INPUT_TOO_LONG),
    ]


def test_empty_string_reports_empty_input() -> None:
    result = FakeEmbeddingProvider().embed_query("")

    assert result.vectors == []
    assert len(result.failures) == 1
    assert result.failures[0].code is EmbeddingErrorCode.EMPTY_INPUT
    assert result.failures[0].input_index == 0


def test_empty_batch_reports_batch_level_empty_input() -> None:
    result = FakeEmbeddingProvider().embed_documents([])

    assert result.vectors == []
    assert len(result.failures) == 1
    assert result.failures[0].code is EmbeddingErrorCode.EMPTY_INPUT
    assert result.failures[0].input_index is None


def test_batch_larger_than_limit_reports_existing_provider_error() -> None:
    result = FakeEmbeddingProvider(max_batch_size=2).embed_documents(["one", "two", "three"])

    assert result.vectors == []
    assert len(result.failures) == 1
    assert result.failures[0].code is EmbeddingErrorCode.PROVIDER_ERROR
    assert result.failures[0].input_index is None


def test_input_byte_limit_accepts_complete_emoji_and_rejects_next_byte() -> None:
    provider = FakeEmbeddingProvider(max_input_bytes=4)

    result = provider.embed_documents(["😀", "😀a"])

    assert [vector.input_index for vector in result.vectors] == [0]
    assert [(failure.input_index, failure.code) for failure in result.failures] == [
        (1, EmbeddingErrorCode.INPUT_TOO_LONG)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "한글 국방",
        "e\u0301",
        "👩🏽\u200d💻",
        "\ud83d",
        "\udc00",
    ],
    ids=["korean", "combining-character", "emoji-zwj", "high-surrogate", "low-surrogate"],
)
def test_unicode_inputs_have_stable_dimension_and_exact_checksum(text: str) -> None:
    provider = FakeEmbeddingProvider(dimension=11, normalized=True)

    first = provider.embed_query(text)
    second = provider.embed_query(text)

    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.vectors) == 1
    assert len(first.vectors[0].values) == 11
    assert first.vectors[0].input_checksum == _checksum(text)


def test_checksum_uses_raw_text_without_stripping_or_unicode_normalization() -> None:
    raw_with_whitespace = "  exact text  "
    composed = "é"
    decomposed = "e\u0301"
    result = FakeEmbeddingProvider().embed_documents([raw_with_whitespace, composed, decomposed])

    assert [vector.input_checksum for vector in result.vectors] == [
        _checksum(raw_with_whitespace),
        _checksum(composed),
        _checksum(decomposed),
    ]
    assert result.vectors[1].input_checksum != result.vectors[2].input_checksum


def test_query_and_document_paths_return_identical_result_shape() -> None:
    provider = FakeEmbeddingProvider(dimension=13, normalized=True)

    query = provider.embed_query("킬체인")
    document = provider.embed_documents(["킬체인"])

    assert query.model_dump_json() == document.model_dump_json()


@pytest.mark.parametrize("dimension", [1, 2, 257])
def test_emitted_vectors_pass_batch_result_dimension_validation(dimension: int) -> None:
    result = FakeEmbeddingProvider(dimension=dimension).embed_documents(["one", "two"])

    reparsed = EmbeddingBatchResult.model_validate(result.model_dump())

    assert reparsed.dimension == dimension
    assert all(len(vector.values) == dimension for vector in reparsed.vectors)


def test_non_positive_integer_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        FakeEmbeddingProvider(dimension=0)
    with pytest.raises(ValueError, match="greater than zero"):
        FakeEmbeddingProvider(dimension=-1)
    with pytest.raises(ValueError, match="greater than zero"):
        FakeEmbeddingProvider(max_batch_size=0)
    with pytest.raises(ValueError, match="greater than zero"):
        FakeEmbeddingProvider(max_input_bytes=0)
