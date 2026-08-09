"""Embedding provider contract.

The interface is deliberately batch-shaped and failure-tolerant: a single bad
input must not discard an otherwise good batch. Every result carries the model
identity, dimension, and normalization flag so an index can refuse to mix
vectors that were produced under different settings.

Secrets and provider raw responses must never appear in returned models. Failure
messages are operator-facing summaries only.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import Checksum, DomainModel, Label


class EmbeddingErrorCode(StrEnum):
    """Stable failure taxonomy shared by every embedding adapter."""

    EMPTY_INPUT = "empty_input"
    INPUT_TOO_LONG = "input_too_long"
    INVALID_DIMENSION = "invalid_dimension"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"


class EmbeddingFailure(DomainModel):
    """One failed input within a batch, located by its position in the request."""

    code: EmbeddingErrorCode
    message: Label
    input_index: NonNegativeInt | None = None


class EmbeddingVector(DomainModel):
    """One embedding with the checksum of the exact text that produced it.

    ``input_checksum`` lets an index detect that stored vectors no longer match
    the current chunk text without keeping the text itself alongside the vector.
    """

    input_index: NonNegativeInt
    input_checksum: Checksum
    values: list[float]

    @model_validator(mode="after")
    def values_must_not_be_empty(self) -> "EmbeddingVector":
        """Reject a zero-length embedding."""
        if not self.values:
            raise ValueError("embedding values must not be empty")
        return self


class EmbeddingBatchResult(DomainModel):
    """Outcome of one embedding request over a batch of inputs."""

    embedding_model_id: Label
    embedding_version: Label
    dimension: PositiveInt
    normalized: bool
    vectors: list[EmbeddingVector] = Field(default_factory=list)
    failures: list[EmbeddingFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def vectors_must_match_declared_dimension(self) -> "EmbeddingBatchResult":
        """Block dimension drift at the contract boundary."""
        for vector in self.vectors:
            if len(vector.values) != self.dimension:
                raise ValueError(
                    "embedding dimension mismatch: "
                    f"expected {self.dimension}, got {len(vector.values)}"
                )
        return self

    @model_validator(mode="after")
    def input_indexes_must_be_unique(self) -> "EmbeddingBatchResult":
        """Keep every returned vector attributable to exactly one input."""
        indexes = [vector.input_index for vector in self.vectors]
        if len(indexes) != len(set(indexes)):
            raise ValueError("input_index must not repeat within one batch result")
        return self

    @model_validator(mode="after")
    def empty_result_must_explain_itself(self) -> "EmbeddingBatchResult":
        """Reject a silent empty batch."""
        if not self.vectors and not self.failures:
            raise ValueError("a batch producing no vectors must record at least one failure")
        return self


class EmbeddingProvider(ABC):
    """Interface implemented by fake, local, and API embedding adapters."""

    @property
    @abstractmethod
    def embedding_model_id(self) -> Label:
        """Stable model identifier recorded on every result and index manifest."""

    @property
    @abstractmethod
    def embedding_version(self) -> Label:
        """Adapter version. Bump whenever produced vectors can change."""

    @property
    @abstractmethod
    def dimension(self) -> PositiveInt:
        """Vector dimension this provider emits."""

    @property
    @abstractmethod
    def normalized(self) -> bool:
        """Whether emitted vectors are unit-normalized."""

    @property
    @abstractmethod
    def max_batch_size(self) -> PositiveInt:
        """Largest batch the caller may submit in one request."""

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatchResult:
        """Embed corpus texts, reporting per-input failures rather than raising."""

    @abstractmethod
    def embed_query(self, text: str) -> EmbeddingBatchResult:
        """Embed one query text.

        Kept separate from :meth:`embed_documents` because providers may apply a
        different prefix or pooling strategy to queries.
        """
