"""Validated models for deterministic chunk-level vector retrieval.

These models describe retrieval mechanics and provenance only. In particular,
they make no claim that an embedding provider preserves semantic similarity.
The offline ``FakeEmbeddingProvider`` is suitable for contract and determinism
tests, not for ranking-quality claims; Recall/MRR belongs to P2.6 once a golden
dataset and a real provider have been selected.
"""

import json
from hashlib import sha256
from typing import Annotated, Final, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import Checksum, DomainModel, Label
from defense_research_agent.domain.publication import PublicationChunk

VECTOR_INDEX_MANIFEST_VERSION: Final[Literal["vector-index-manifest-v1"]] = (
    "vector-index-manifest-v1"
)
VECTOR_ENTRIES_FILENAME: Final[Literal["vectors.jsonl"]] = "vectors.jsonl"
VECTOR_MANIFEST_FILENAME: Final[Literal["vector-index.manifest.json"]] = (
    "vector-index.manifest.json"
)
VECTOR_SIMILARITY_METRIC: Final[Literal["cosine"]] = "cosine"
VECTOR_TIE_BREAKER: Final[Literal["score-desc,publication-id-asc,chunk-index-asc,chunk-id-asc"]] = (
    "score-desc,publication-id-asc,chunk-index-asc,chunk-id-asc"
)

type VectorNormalization = Literal["l2", "none"]
type SimilarityScore = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]


class VectorIndexManifest(DomainModel):
    """Content address and compatibility contract for one vector index.

    The content address binds the complete compatibility settings, canonical
    input-chunk checksum, and canonical vector-entry checksum. No wall-clock or
    filesystem-specific field is present, so the same inputs, settings, and
    deterministic provider produce byte-identical canonical manifest bytes.
    """

    manifest_version: Literal["vector-index-manifest-v1"] = VECTOR_INDEX_MANIFEST_VERSION
    embedding_model_id: Label
    embedding_version: Label
    dimension: PositiveInt
    normalization: VectorNormalization
    chunking_version: Label
    input_chunk_count: NonNegativeInt
    input_chunks_sha256: Checksum
    indexed_chunk_count: NonNegativeInt
    vector_entries_filename: Literal["vectors.jsonl"] = VECTOR_ENTRIES_FILENAME
    vector_entries_sha256: Checksum
    vector_entries_size_bytes: NonNegativeInt
    similarity_metric: Literal["cosine"] = VECTOR_SIMILARITY_METRIC
    tie_breaker: Literal["score-desc,publication-id-asc,chunk-index-asc,chunk-id-asc"] = (
        VECTOR_TIE_BREAKER
    )
    content_address: Checksum

    @model_validator(mode="after")
    def every_input_chunk_must_be_indexed(self) -> "VectorIndexManifest":
        """Reject partial indexes and fields not bound by the content address."""
        if self.indexed_chunk_count != self.input_chunk_count:
            raise ValueError("indexed_chunk_count must equal input_chunk_count")
        address_payload = json.dumps(
            self.model_dump(mode="json", exclude={"content_address"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if sha256(address_payload).hexdigest() != self.content_address:
            raise ValueError("content_address does not match the vector index manifest")
        return self


class VectorSearchMatch(DomainModel):
    """One scored chunk with complete publication, page, and parser provenance."""

    score: SimilarityScore
    chunk: PublicationChunk

    @property
    def publication_id(self) -> str:
        """Expose the legacy publication-level identifier without flattening the chunk."""
        return self.chunk.publication_id

    @property
    def chunk_id(self) -> str:
        """Return the stable chunk identifier used by deterministic tie-breaking."""
        return self.chunk.chunk_id

    def page_number_for_offset(self, offset: int) -> int:
        """Resolve one half-open chunk-text offset to its original page number."""
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise TypeError("offset must be an int")
        if offset < 0 or offset >= len(self.chunk.text):
            raise ValueError("offset must locate a character within the chunk text")
        for span in self.chunk.page_spans:
            if span.start_offset <= offset < span.end_offset:
                return span.page_number
        raise RuntimeError("validated page spans did not cover the requested offset")
