"""Embedding provider contract tests, exercised through a fake adapter."""

from collections.abc import Sequence
from hashlib import sha256

import pytest
from pydantic import ValidationError

from defense_research_agent.search import (
    EmbeddingBatchResult,
    EmbeddingErrorCode,
    EmbeddingFailure,
    EmbeddingProvider,
    EmbeddingVector,
)

DIMENSION = 4


class FakeHashEmbeddingProvider(EmbeddingProvider):
    """Deterministic hash-based adapter proving the interface is implementable.

    The vectors carry no semantic meaning. They exist to exercise the pipeline
    and the contract, never to justify a ranking claim.
    """

    @property
    def embedding_model_id(self) -> str:
        return "fake-hash"

    @property
    def embedding_version(self) -> str:
        return "0.1.0"

    @property
    def dimension(self) -> int:
        return DIMENSION

    @property
    def normalized(self) -> bool:
        return False

    @property
    def max_batch_size(self) -> int:
        return 8

    def _vector(self, index: int, text: str) -> EmbeddingVector:
        digest = sha256(text.encode("utf-8"))
        values = [digest.digest()[position] / 255.0 for position in range(DIMENSION)]
        return EmbeddingVector(
            input_index=index,
            input_checksum=digest.hexdigest(),
            values=values,
        )

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatchResult:
        vectors: list[EmbeddingVector] = []
        failures: list[EmbeddingFailure] = []
        for index, text in enumerate(texts):
            if not text.strip():
                failures.append(
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.EMPTY_INPUT,
                        message="빈 입력",
                        input_index=index,
                    )
                )
                continue
            vectors.append(self._vector(index, text))
        if not texts:
            failures.append(
                EmbeddingFailure(code=EmbeddingErrorCode.EMPTY_INPUT, message="빈 배치")
            )
        return EmbeddingBatchResult(
            embedding_model_id=self.embedding_model_id,
            embedding_version=self.embedding_version,
            dimension=self.dimension,
            normalized=self.normalized,
            vectors=vectors,
            failures=failures,
        )

    def embed_query(self, text: str) -> EmbeddingBatchResult:
        return self.embed_documents([text])


def _result(**overrides: object) -> EmbeddingBatchResult:
    values: dict[str, object] = {
        "embedding_model_id": "fake-hash",
        "embedding_version": "0.1.0",
        "dimension": DIMENSION,
        "normalized": False,
        "vectors": [
            EmbeddingVector(
                input_index=0,
                input_checksum="c" * 64,
                values=[0.1] * DIMENSION,
            )
        ],
    }
    values.update(overrides)
    return EmbeddingBatchResult.model_validate(values)


def test_same_input_produces_byte_equivalent_vectors() -> None:
    provider = FakeHashEmbeddingProvider()

    first = provider.embed_documents(["국방 정책 연구"])
    second = provider.embed_documents(["국방 정책 연구"])

    assert first.model_dump_json() == second.model_dump_json()


def test_one_bad_input_does_not_discard_the_rest_of_the_batch() -> None:
    provider = FakeHashEmbeddingProvider()

    result = provider.embed_documents(["첫 문장", "   ", "셋째 문장"])

    assert [vector.input_index for vector in result.vectors] == [0, 2]
    assert [failure.input_index for failure in result.failures] == [1]
    assert result.failures[0].code is EmbeddingErrorCode.EMPTY_INPUT


def test_empty_batch_reports_a_failure_instead_of_returning_nothing() -> None:
    provider = FakeHashEmbeddingProvider()

    result = provider.embed_documents([])

    assert result.vectors == []
    assert result.failures[0].code is EmbeddingErrorCode.EMPTY_INPUT


def test_query_and_document_paths_share_the_result_shape() -> None:
    provider = FakeHashEmbeddingProvider()

    query = provider.embed_query("킬체인")
    document = provider.embed_documents(["킬체인"])

    assert query.model_dump_json() == document.model_dump_json()


def test_vector_checksum_tracks_the_exact_input_text() -> None:
    provider = FakeHashEmbeddingProvider()

    result = provider.embed_documents(["킬체인"])

    assert result.vectors[0].input_checksum == sha256("킬체인".encode()).hexdigest()


def test_dimension_mismatch_is_blocked_at_the_contract_boundary() -> None:
    with pytest.raises(ValidationError, match="dimension mismatch"):
        _result(
            vectors=[
                EmbeddingVector(
                    input_index=0,
                    input_checksum="c" * 64,
                    values=[0.1, 0.2],
                )
            ]
        )


def test_input_index_must_not_repeat() -> None:
    with pytest.raises(ValidationError, match="must not repeat"):
        _result(
            vectors=[
                EmbeddingVector(input_index=0, input_checksum="c" * 64, values=[0.1] * DIMENSION),
                EmbeddingVector(input_index=0, input_checksum="d" * 64, values=[0.2] * DIMENSION),
            ]
        )


def test_result_without_vectors_or_failures_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one failure"):
        _result(vectors=[])


def test_zero_length_vector_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        EmbeddingVector(input_index=0, input_checksum="c" * 64, values=[])


def test_dimension_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _result(dimension=0)
