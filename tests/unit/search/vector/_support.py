"""Offline fixtures for vector-index and search tests."""

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

SOURCE_CHECKSUM = "a" * 64
CHUNKING_VERSION = "fixture-chunks-v1"
PROVENANCE = ExtractionProvenance(
    parser_name="fixture-parser",
    parser_version="1.0.0",
    source_checksum=SOURCE_CHECKSUM,
)


def make_chunk(
    publication_id: str,
    chunk_index: int,
    text: str,
    *,
    chunk_id: str | None = None,
    page_start: int = 1,
    page_spans: list[PublicationPageSpan] | None = None,
    chunking_version: str = CHUNKING_VERSION,
    checksum: str | None = None,
) -> PublicationChunk:
    """Build a complete validated page-aware chunk without repository data."""
    selected_spans = page_spans or [
        PublicationPageSpan(
            page_number=page_start,
            start_offset=0,
            end_offset=len(text),
        )
    ]
    return PublicationChunk(
        chunk_id=chunk_id or f"chunk:{publication_id.removeprefix('pub:')}:{chunk_index}",
        publication_id=publication_id,
        text=text,
        page_start=page_start,
        page_end=selected_spans[-1].page_number,
        page_spans=selected_spans,
        provenance=PROVENANCE,
        chunk_index=chunk_index,
        checksum=checksum or sha256(text.encode("utf-8")).hexdigest(),
        chunking_version=chunking_version,
    )


class StaticEmbeddingProvider(EmbeddingProvider):
    """Return caller-selected vectors with exact text checksums and no network."""

    def __init__(
        self,
        *,
        vectors: Mapping[str, Sequence[float]] | None = None,
        default_vector: Sequence[float] = (1.0, 0.0),
        embedding_model_id: str = "fixture-static",
        embedding_version: str = "1.0.0",
        dimension: int = 2,
        normalized: bool = True,
        fail_documents: bool = False,
        fail_query: bool = False,
    ) -> None:
        self._vectors = {} if vectors is None else dict(vectors)
        self._default_vector = list(default_vector)
        self._embedding_model_id = embedding_model_id
        self._embedding_version = embedding_version
        self._dimension = dimension
        self._normalized = normalized
        self._fail_documents = fail_documents
        self._fail_query = fail_query
        self.query_call_count = 0

    @property
    def embedding_model_id(self) -> str:
        return self._embedding_model_id

    @property
    def embedding_version(self) -> str:
        return self._embedding_version

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def normalized(self) -> bool:
        return self._normalized

    @property
    def max_batch_size(self) -> int:
        return 2

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatchResult:
        if self._fail_documents:
            return self._result(
                failures=[
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.PROVIDER_ERROR,
                        message="fixture document failure",
                        input_index=0,
                    )
                ]
            )
        return self._embed(texts)

    def embed_query(self, text: str) -> EmbeddingBatchResult:
        self.query_call_count += 1
        if self._fail_query:
            return self._result(
                failures=[
                    EmbeddingFailure(
                        code=EmbeddingErrorCode.PROVIDER_ERROR,
                        message="fixture query failure",
                        input_index=0,
                    )
                ]
            )
        return self._embed([text])

    def _embed(self, texts: Sequence[str]) -> EmbeddingBatchResult:
        vectors = [
            EmbeddingVector(
                input_index=input_index,
                input_checksum=sha256(text.encode("utf-8")).hexdigest(),
                values=list(self._vectors.get(text, self._default_vector)),
            )
            for input_index, text in enumerate(texts)
        ]
        return self._result(vectors=vectors)

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
