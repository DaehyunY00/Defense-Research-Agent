"""Dependency-free vector index abstraction and canonical artifact writer."""

import json
import math
from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import Field, model_validator

from defense_research_agent.domain.common import DomainModel
from defense_research_agent.domain.publication import PublicationChunk
from defense_research_agent.path_safety import ensure_outside_read_only_data
from defense_research_agent.search.embeddings.base import (
    EmbeddingBatchResult,
    EmbeddingProvider,
)
from defense_research_agent.search.vector.models import (
    VECTOR_ENTRIES_FILENAME,
    VECTOR_INDEX_MANIFEST_VERSION,
    VECTOR_MANIFEST_FILENAME,
    VECTOR_SIMILARITY_METRIC,
    VECTOR_TIE_BREAKER,
    VectorIndexManifest,
    VectorNormalization,
    VectorSearchMatch,
)


class VectorIndexError(ValueError):
    """Base error for invalid vector-index state or input."""


class VectorIndexBuildError(VectorIndexError):
    """Raised when a complete, attributable index cannot be built."""


class VectorIndexNotBuiltError(VectorIndexError):
    """Raised when an operation needs an index manifest that does not exist."""


class _VectorIndexRecord(DomainModel):
    chunk: PublicationChunk
    values: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def vector_values_must_be_finite(self) -> "_VectorIndexRecord":
        if not all(math.isfinite(value) for value in self.values):
            raise ValueError("vector values must be finite")
        if math.fsum(value * value for value in self.values) == 0.0:
            raise ValueError("vector values must not have zero L2 norm")
        return self


@dataclass(frozen=True, slots=True)
class _StoredVector:
    record: _VectorIndexRecord
    l2_norm: float


class VectorIndex(ABC):
    """Chunk-level index boundary independent from a storage or ANN backend."""

    @property
    @abstractmethod
    def manifest(self) -> VectorIndexManifest | None:
        """Return the immutable build contract, or ``None`` before a build."""

    @abstractmethod
    def build(
        self,
        chunks: Sequence[PublicationChunk],
        embedding_provider: EmbeddingProvider,
        *,
        chunking_version: str,
    ) -> VectorIndexManifest:
        """Replace the index with vectors for every supplied chunk, fail-closed."""

    @abstractmethod
    def nearest(
        self,
        query_vector: Sequence[float],
        *,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[VectorSearchMatch]:
        """Return cosine-ranked chunks under a deterministic total ordering."""

    @abstractmethod
    def canonical_entries(self) -> bytes:
        """Serialize canonical vector entries for content-addressed persistence."""


class InMemoryVectorIndex(VectorIndex):
    """Exact cosine index used as the deterministic local vector baseline.

    It intentionally performs a linear scan. This lane establishes contracts,
    provenance, artifact determinism, and fail-closed compatibility checks; it
    does not claim semantic quality or approximate-nearest-neighbor performance.
    """

    def __init__(self) -> None:
        self._manifest: VectorIndexManifest | None = None
        self._vectors: tuple[_StoredVector, ...] = ()
        self._entries_payload: bytes | None = None

    @property
    def manifest(self) -> VectorIndexManifest | None:
        """Return the manifest produced by the latest successful build."""
        return self._manifest

    def build(
        self,
        chunks: Sequence[PublicationChunk],
        embedding_provider: EmbeddingProvider,
        *,
        chunking_version: str,
    ) -> VectorIndexManifest:
        """Embed every chunk in deterministic order and atomically replace state."""
        normalized_chunking_version = chunking_version.strip()
        if not normalized_chunking_version:
            raise VectorIndexBuildError("chunking_version must not be blank")
        ordered_chunks = tuple(sorted(chunks, key=_chunk_order_key))
        _validate_chunks(ordered_chunks, chunking_version=normalized_chunking_version)
        provider_settings = _provider_settings(embedding_provider)

        records: list[_VectorIndexRecord] = []
        max_batch_size = embedding_provider.max_batch_size
        if (
            isinstance(max_batch_size, bool)
            or not isinstance(max_batch_size, int)
            or max_batch_size <= 0
        ):
            raise VectorIndexBuildError("embedding provider max_batch_size must be positive")

        for batch_start in range(0, len(ordered_chunks), max_batch_size):
            batch = ordered_chunks[batch_start : batch_start + max_batch_size]
            try:
                result = embedding_provider.embed_documents([chunk.text for chunk in batch])
            except Exception as error:
                raise VectorIndexBuildError(
                    "embedding provider failed while building the vector index"
                ) from error
            _validate_embedding_result(
                result,
                expected_settings=provider_settings,
                expected_input_count=len(batch),
                operation="document",
            )
            vectors_by_index = {vector.input_index: vector for vector in result.vectors}
            for input_index, chunk in enumerate(batch):
                vector = vectors_by_index[input_index]
                if vector.input_checksum != chunk.checksum:
                    raise VectorIndexBuildError(
                        "document embedding checksum does not match its chunk text"
                    )
                try:
                    record = _VectorIndexRecord(chunk=chunk, values=vector.values)
                except ValueError as error:
                    raise VectorIndexBuildError("document embedding vector is invalid") from error
                records.append(record)

        entries_payload = b"".join(_canonical_json_line(record) for record in records)
        chunks_payload = b"".join(_canonical_json_line(chunk) for chunk in ordered_chunks)
        manifest_fields: dict[str, object] = {
            "manifest_version": VECTOR_INDEX_MANIFEST_VERSION,
            **provider_settings,
            "chunking_version": normalized_chunking_version,
            "input_chunk_count": len(ordered_chunks),
            "input_chunks_sha256": sha256(chunks_payload).hexdigest(),
            "indexed_chunk_count": len(records),
            "vector_entries_filename": VECTOR_ENTRIES_FILENAME,
            "vector_entries_sha256": sha256(entries_payload).hexdigest(),
            "vector_entries_size_bytes": len(entries_payload),
            "similarity_metric": VECTOR_SIMILARITY_METRIC,
            "tie_breaker": VECTOR_TIE_BREAKER,
        }
        content_address = sha256(_canonical_json_bytes(manifest_fields)).hexdigest()
        manifest = VectorIndexManifest.model_validate(
            {**manifest_fields, "content_address": content_address}
        )

        self._vectors = tuple(
            _StoredVector(record=record, l2_norm=_l2_norm(record.values)) for record in records
        )
        self._entries_payload = entries_payload
        self._manifest = manifest
        return manifest

    def nearest(
        self,
        query_vector: Sequence[float],
        *,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[VectorSearchMatch]:
        """Rank exact cosine scores, then apply the declared stable tie-breaker."""
        if limit <= 0:
            return []
        manifest = self._require_manifest()
        if len(query_vector) != manifest.dimension:
            raise VectorIndexError(
                "query vector dimension does not match the vector index manifest"
            )
        if not all(math.isfinite(value) for value in query_vector):
            raise VectorIndexError("query vector values must be finite")
        query_norm = _l2_norm(query_vector)
        allowed_ids = None if allowed_publication_ids is None else set(allowed_publication_ids)

        matches: list[VectorSearchMatch] = []
        for stored in self._vectors:
            chunk = stored.record.chunk
            if allowed_ids is not None and chunk.publication_id not in allowed_ids:
                continue
            dot_product = math.fsum(
                query_value * document_value
                for query_value, document_value in zip(
                    query_vector,
                    stored.record.values,
                    strict=True,
                )
            )
            score = dot_product / (query_norm * stored.l2_norm)
            score = min(1.0, max(-1.0, score))
            matches.append(VectorSearchMatch(score=score, chunk=chunk))

        matches.sort(
            key=lambda match: (
                -match.score,
                match.publication_id,
                match.chunk.chunk_index,
                match.chunk_id,
            )
        )
        return matches[:limit]

    def canonical_entries(self) -> bytes:
        """Return the exact payload bound by ``manifest.vector_entries_sha256``."""
        self._require_manifest()
        if self._entries_payload is None:
            raise RuntimeError("built vector index is missing its canonical payload")
        return self._entries_payload

    def _require_manifest(self) -> VectorIndexManifest:
        if self._manifest is None:
            raise VectorIndexNotBuiltError("vector index has not been built")
        return self._manifest


def write_vector_index_artifacts(
    index: VectorIndex,
    output_directory: Path,
) -> VectorIndexManifest:
    """Write canonical index and manifest files outside read-only ``data/``."""
    ensure_outside_read_only_data(output_directory)
    manifest = index.manifest
    if manifest is None:
        raise VectorIndexNotBuiltError("vector index has not been built")
    entries_payload = index.canonical_entries()
    if len(entries_payload) != manifest.vector_entries_size_bytes:
        raise VectorIndexError("vector entry payload size does not match its manifest")
    if sha256(entries_payload).hexdigest() != manifest.vector_entries_sha256:
        raise VectorIndexError("vector entry payload checksum does not match its manifest")

    manifest_payload = _canonical_json_line(manifest)
    resolved_output = output_directory.resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(resolved_output / VECTOR_ENTRIES_FILENAME, entries_payload)
    _atomic_write_bytes(resolved_output / VECTOR_MANIFEST_FILENAME, manifest_payload)
    return manifest


def canonical_vector_manifest_bytes(manifest: VectorIndexManifest) -> bytes:
    """Return the stable UTF-8 JSON-line representation used by the writer."""
    return _canonical_json_line(manifest)


def _provider_settings(embedding_provider: EmbeddingProvider) -> dict[str, object]:
    dimension = embedding_provider.dimension
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise VectorIndexBuildError("embedding provider dimension must be positive")
    normalized = embedding_provider.normalized
    if not isinstance(normalized, bool):
        raise VectorIndexBuildError("embedding provider normalized must be a bool")
    return {
        "embedding_model_id": embedding_provider.embedding_model_id,
        "embedding_version": embedding_provider.embedding_version,
        "dimension": dimension,
        "normalization": _normalization_name(normalized),
    }


def _validate_chunks(
    chunks: Sequence[PublicationChunk],
    *,
    chunking_version: str,
) -> None:
    chunk_ids: set[str] = set()
    chunk_positions: set[tuple[str, int]] = set()
    for chunk in chunks:
        if chunk.chunk_id in chunk_ids:
            raise VectorIndexBuildError("chunk_id must be unique within a vector index")
        chunk_ids.add(chunk.chunk_id)
        position = (chunk.publication_id, chunk.chunk_index)
        if position in chunk_positions:
            raise VectorIndexBuildError(
                "publication_id and chunk_index must be unique within a vector index"
            )
        chunk_positions.add(position)
        if chunk.chunking_version != chunking_version:
            raise VectorIndexBuildError(
                "chunk version does not match the configured chunking_version"
            )
        actual_checksum = sha256(chunk.text.encode("utf-8")).hexdigest()
        if chunk.checksum != actual_checksum:
            raise VectorIndexBuildError("chunk checksum does not match its exact text")


def _validate_embedding_result(
    result: EmbeddingBatchResult,
    *,
    expected_settings: dict[str, object],
    expected_input_count: int,
    operation: str,
) -> None:
    observed_settings = {
        "embedding_model_id": result.embedding_model_id,
        "embedding_version": result.embedding_version,
        "dimension": result.dimension,
        "normalization": _normalization_name(result.normalized),
    }
    for field_name, expected_value in expected_settings.items():
        if observed_settings[field_name] != expected_value:
            raise VectorIndexBuildError(
                f"{operation} embedding {field_name} does not match provider settings"
            )
    if result.failures:
        failure_locations = ",".join(
            "batch" if failure.input_index is None else str(failure.input_index)
            for failure in result.failures
        )
        raise VectorIndexBuildError(
            f"{operation} embedding failed for input positions: {failure_locations}"
        )
    indexes = {vector.input_index for vector in result.vectors}
    if indexes != set(range(expected_input_count)):
        raise VectorIndexBuildError(
            f"{operation} embedding did not return exactly one vector per input"
        )


def _normalization_name(normalized: bool) -> VectorNormalization:
    return "l2" if normalized else "none"


def _chunk_order_key(chunk: PublicationChunk) -> tuple[str, int, str]:
    return (chunk.publication_id, chunk.chunk_index, chunk.chunk_id)


def _l2_norm(values: Sequence[float]) -> float:
    squared_norm = math.fsum(value * value for value in values)
    if squared_norm == 0.0:
        raise VectorIndexError("vector values must not have zero L2 norm")
    return math.sqrt(squared_norm)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_json_line(model: DomainModel) -> bytes:
    return _canonical_json_bytes(model.model_dump(mode="json")) + b"\n"


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)
