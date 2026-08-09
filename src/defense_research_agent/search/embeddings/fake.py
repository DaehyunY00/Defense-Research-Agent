"""Deterministic offline embedding adapter for contract and pipeline tests."""

from collections.abc import Sequence
from hashlib import sha256

from defense_research_agent.search.embeddings.base import (
    EmbeddingBatchResult,
    EmbeddingErrorCode,
    EmbeddingFailure,
    EmbeddingProvider,
    EmbeddingVector,
)

DEFAULT_DIMENSION = 8
DEFAULT_MAX_BATCH_SIZE = 32
DEFAULT_MAX_INPUT_BYTES = 8_192


class FakeEmbeddingProvider(EmbeddingProvider):
    """Produce deterministic vectors without a model, network, or credentials.

    SHA-256 over the exact input bytes selects one coordinate, its sign, and an
    integer magnitude. When ``normalized`` is true, that magnitude is replaced
    by ``1.0``, producing an exactly unit-normalized one-hot vector. This makes
    the serialized floats independent of platform math libraries as well as
    process hash randomization.

    Text is encoded with UTF-8 and ``surrogatepass``. It is not stripped or
    Unicode-normalized before hashing, so ``input_checksum`` covers the exact
    supplied text (including whitespace and combining-character form). Stripping
    is used only to decide whether an input is empty.

    The generated vectors deliberately do not preserve lexical or semantic
    similarity. Collisions and distances are arbitrary, so this provider is only
    suitable for offline interface and pipeline tests. It must not be used to
    support ranking-quality claims or retrieval benchmark comparisons.
    """

    def __init__(
        self,
        *,
        dimension: int = DEFAULT_DIMENSION,
        normalized: bool = False,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    ) -> None:
        """Configure output shape and deterministic input limits."""
        self._dimension = self._positive_int(dimension, name="dimension")
        if not isinstance(normalized, bool):
            raise TypeError("normalized must be a bool")
        self._normalized = normalized
        self._max_batch_size = self._positive_int(max_batch_size, name="max_batch_size")
        self._max_input_bytes = self._positive_int(max_input_bytes, name="max_input_bytes")

    @staticmethod
    def _positive_int(value: int, *, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an int")
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")
        return value

    @property
    def embedding_model_id(self) -> str:
        """Return the stable identifier for this non-semantic fake algorithm."""
        return "fake-sha256-axis"

    @property
    def embedding_version(self) -> str:
        """Return the vector-generation algorithm version."""
        return "1.0.0"

    @property
    def dimension(self) -> int:
        """Return the configured number of vector coordinates."""
        return self._dimension

    @property
    def normalized(self) -> bool:
        """Report whether emitted vectors have unit L2 norm."""
        return self._normalized

    @property
    def max_batch_size(self) -> int:
        """Return the largest accepted document batch."""
        return self._max_batch_size

    @property
    def max_input_bytes(self) -> int:
        """Return the UTF-8-with-surrogatepass byte limit for one input."""
        return self._max_input_bytes

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatchResult:
        """Embed valid inputs and preserve per-input failures in batch order.

        A batch larger than ``max_batch_size`` is rejected as one batch-level
        ``PROVIDER_ERROR`` because the existing contract has no dedicated batch
        size error code. Empty and overlong individual inputs use ``EMPTY_INPUT``
        and ``INPUT_TOO_LONG``, respectively.
        """
        if not texts:
            return self._result(
                failures=[
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.EMPTY_INPUT,
                        message="embedding batch must not be empty",
                    )
                ]
            )
        if len(texts) > self.max_batch_size:
            return self._result(
                failures=[
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.PROVIDER_ERROR,
                        message=(
                            f"batch size {len(texts)} exceeds max_batch_size {self.max_batch_size}"
                        ),
                    )
                ]
            )

        vectors: list[EmbeddingVector] = []
        failures: list[EmbeddingFailure] = []
        for input_index, text in enumerate(texts):
            if not text.strip():
                failures.append(
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.EMPTY_INPUT,
                        message="embedding input must not be empty",
                        input_index=input_index,
                    )
                )
                continue

            input_bytes = text.encode("utf-8", errors="surrogatepass")
            if len(input_bytes) > self.max_input_bytes:
                failures.append(
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.INPUT_TOO_LONG,
                        message=(
                            f"embedding input is {len(input_bytes)} bytes; "
                            f"maximum is {self.max_input_bytes}"
                        ),
                        input_index=input_index,
                    )
                )
                continue

            vectors.append(self._vector(input_index=input_index, input_bytes=input_bytes))

        return self._result(vectors=vectors, failures=failures)

    def embed_query(self, text: str) -> EmbeddingBatchResult:
        """Embed one query through the same deterministic path as documents."""
        return self.embed_documents([text])

    def _vector(self, *, input_index: int, input_bytes: bytes) -> EmbeddingVector:
        digest = sha256(input_bytes).digest()
        coordinate = int.from_bytes(digest[:8], byteorder="big") % self.dimension
        sign = -1.0 if digest[8] & 1 else 1.0
        magnitude = 1.0 if self.normalized else float(digest[9] + 2)
        values = [0.0] * self.dimension
        values[coordinate] = sign * magnitude
        return EmbeddingVector(
            input_index=input_index,
            input_checksum=digest.hex(),
            values=values,
        )

    def _result(
        self,
        *,
        vectors: list[EmbeddingVector] | None = None,
        failures: list[EmbeddingFailure] | None = None,
    ) -> EmbeddingBatchResult:
        return EmbeddingBatchResult(
            embedding_model_id=self.embedding_model_id,
            embedding_version=self.embedding_version,
            dimension=self.dimension,
            normalized=self.normalized,
            vectors=[] if vectors is None else vectors,
            failures=[] if failures is None else failures,
        )
