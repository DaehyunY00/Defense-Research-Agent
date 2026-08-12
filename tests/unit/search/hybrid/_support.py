"""Deterministic hybrid-search fixtures without filesystem or network access."""

from collections.abc import Mapping, Sequence
from hashlib import sha256

from defense_research_agent.domain import (
    ExtractionProvenance,
    PublicationChunk,
    PublicationPageSpan,
)
from defense_research_agent.search.embeddings.base import (
    EmbeddingBatchResult,
    EmbeddingErrorCode,
    EmbeddingFailure,
    EmbeddingProvider,
    EmbeddingVector,
)

CHUNKING_VERSION = "hybrid-fixture-chunks-v1"
PROVENANCE = ExtractionProvenance(
    parser_name="hybrid-fixture-parser",
    parser_version="1.0.0",
    source_checksum="a" * 64,
)


def make_chunk(
    publication_id: str,
    chunk_index: int,
    text: str,
) -> PublicationChunk:
    """Build one complete page-aware chunk for hybrid tests."""
    return PublicationChunk(
        chunk_id=f"chunk:{publication_id.removeprefix('pub:')}:{chunk_index}",
        publication_id=publication_id,
        text=text,
        page_start=1,
        page_end=1,
        page_spans=[PublicationPageSpan(page_number=1, start_offset=0, end_offset=len(text))],
        provenance=PROVENANCE,
        chunk_index=chunk_index,
        checksum=sha256(text.encode("utf-8")).hexdigest(),
        chunking_version=CHUNKING_VERSION,
    )


class StaticEmbeddingProvider(EmbeddingProvider):
    """Return selected two-dimensional vectors with deterministic metadata."""

    def __init__(
        self,
        vectors: Mapping[str, Sequence[float]],
        *,
        fail_query: bool = False,
    ) -> None:
        self._vectors = {text: list(vector) for text, vector in vectors.items()}
        self._fail_query = fail_query

    @property
    def embedding_model_id(self) -> str:
        return "hybrid-static"

    @property
    def embedding_version(self) -> str:
        return "1.0.0"

    @property
    def dimension(self) -> int:
        return 2

    @property
    def normalized(self) -> bool:
        return True

    @property
    def max_batch_size(self) -> int:
        return 32

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatchResult:
        return self._embed(texts)

    def embed_query(self, text: str) -> EmbeddingBatchResult:
        if self._fail_query:
            return self._result(
                failures=[
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.PROVIDER_ERROR,
                        message="simulated hybrid query failure",
                        input_index=0,
                    )
                ]
            )
        return self._embed([text])

    def _embed(self, texts: Sequence[str]) -> EmbeddingBatchResult:
        return self._result(
            vectors=[
                EmbeddingVector(
                    input_index=index,
                    input_checksum=sha256(text.encode("utf-8")).hexdigest(),
                    values=self._vectors[text],
                )
                for index, text in enumerate(texts)
            ]
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
