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

from pydantic import Field, NonNegativeInt, PositiveInt, field_validator, model_validator

from defense_research_agent.domain.common import Checksum, DomainModel, Label
from defense_research_agent.domain.publication import PublicationChunk
from defense_research_agent.search.embeddings.base import EmbeddingErrorCode

VECTOR_INDEX_MANIFEST_VERSION: Final[Literal["vector-index-manifest-v2"]] = (
    "vector-index-manifest-v2"
)
VECTOR_ENTRIES_FILENAME: Final[Literal["vectors.jsonl"]] = "vectors.jsonl"
VECTOR_MANIFEST_FILENAME: Final[Literal["vector-index.manifest.json"]] = (
    "vector-index.manifest.json"
)
VECTOR_SIMILARITY_METRIC: Final[Literal["cosine"]] = "cosine"
VECTOR_INDEX_FAILURE_POLICY: Final[Literal["skip-unembeddable-inputs-v1"]] = (
    "skip-unembeddable-inputs-v1"
)
VECTOR_TIE_BREAKER: Final[Literal["score-desc,publication-id-asc,chunk-index-asc,chunk-id-asc"]] = (
    "score-desc,publication-id-asc,chunk-index-asc,chunk-id-asc"
)

type VectorNormalization = Literal["l2", "none"]
type SimilarityScore = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]


class VectorIndexSkippedChunk(DomainModel):
    """One chunk omitted under the explicit, content-attributable failure policy."""

    chunk_id: Label
    publication_id: Label
    chunk_index: NonNegativeInt
    chunk_checksum: Checksum
    input_size_bytes: NonNegativeInt
    failure_code: EmbeddingErrorCode
    failure_message: Label

    @field_validator("failure_code")
    @classmethod
    def failure_code_must_be_skippable(
        cls,
        value: EmbeddingErrorCode,
    ) -> EmbeddingErrorCode:
        """Keep operational failures out of manifests for successful builds."""
        if value not in {
            EmbeddingErrorCode.EMPTY_INPUT,
            EmbeddingErrorCode.INPUT_TOO_LONG,
        }:
            raise ValueError("failure_code must be content-attributable and skippable")
        return value


class VectorIndexManifest(DomainModel):
    """Content address and compatibility contract for one vector index.

    The content address binds the complete compatibility settings, canonical
    input-chunk checksum, and canonical vector-entry checksum. No wall-clock or
    filesystem-specific field is present, so the same inputs, settings, and
    deterministic provider produce byte-identical canonical manifest bytes.
    """

    manifest_version: Literal["vector-index-manifest-v2"] = VECTOR_INDEX_MANIFEST_VERSION
    embedding_model_id: Label
    embedding_version: Label
    dimension: PositiveInt
    normalization: VectorNormalization
    chunking_version: Label
    input_chunk_count: NonNegativeInt
    input_chunks_sha256: Checksum
    indexed_chunk_count: NonNegativeInt
    skipped_chunk_count: NonNegativeInt
    skipped_chunks: list[VectorIndexSkippedChunk] = Field(default_factory=list)
    failure_policy: Literal["skip-unembeddable-inputs-v1"] = VECTOR_INDEX_FAILURE_POLICY
    vector_entries_filename: Literal["vectors.jsonl"] = VECTOR_ENTRIES_FILENAME
    vector_entries_sha256: Checksum
    vector_entries_size_bytes: NonNegativeInt
    similarity_metric: Literal["cosine"] = VECTOR_SIMILARITY_METRIC
    tie_breaker: Literal["score-desc,publication-id-asc,chunk-index-asc,chunk-id-asc"] = (
        VECTOR_TIE_BREAKER
    )
    content_address: Checksum

    @model_validator(mode="after")
    def validate_chunk_accounting_and_content_address(self) -> "VectorIndexManifest":
        """Require every input to be indexed or attributed to one recorded skip."""
        if self.skipped_chunk_count != len(self.skipped_chunks):
            raise ValueError("skipped_chunk_count must equal the skipped_chunks length")
        if self.indexed_chunk_count + self.skipped_chunk_count != self.input_chunk_count:
            raise ValueError("indexed and skipped chunk counts must account for every input chunk")
        skipped_ids = [skipped.chunk_id for skipped in self.skipped_chunks]
        if len(skipped_ids) != len(set(skipped_ids)):
            raise ValueError("skipped chunk_id must be unique")
        skipped_positions = [
            (skipped.publication_id, skipped.chunk_index) for skipped in self.skipped_chunks
        ]
        if len(skipped_positions) != len(set(skipped_positions)):
            raise ValueError("skipped publication_id and chunk_index must be unique")
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
