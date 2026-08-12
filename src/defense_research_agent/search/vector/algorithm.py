"""Chunk-level vector search with explicit legacy-result projection."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, Literal, Protocol

from defense_research_agent.domain import PublicationChunk, ResearchPublication, SearchField
from defense_research_agent.search.base import PublicationSearchAlgorithm, SearchMatch
from defense_research_agent.search.embeddings.base import EmbeddingProvider
from defense_research_agent.search.vector.index import (
    InMemoryVectorIndex,
    VectorIndex,
    VectorIndexError,
    VectorIndexNotBuiltError,
)
from defense_research_agent.search.vector.models import VectorIndexManifest, VectorSearchMatch


class VectorSearchError(ValueError):
    """Base error raised at the vector-search orchestration boundary."""


class VectorSearchConfigurationError(VectorSearchError):
    """Raised before search when index and current query settings differ."""


class VectorQueryEmbeddingError(VectorSearchError):
    """Raised when a query cannot produce one attributable compatible vector."""


LEGACY_COSINE_SCORE_MAPPING: Final[Literal["(cosine + 1) / 2"]] = "(cosine + 1) / 2"


@dataclass(frozen=True, slots=True)
class LegacyVectorSearchMatch(SearchMatch):
    """Legacy-compatible score plus the original cosine and mapping disclosure.

    The legacy repository contract requires a non-negative score, while cosine
    spans ``[-1, 1]``. ``score`` therefore uses ``(cosine + 1) / 2``, a monotonic
    affine mapping into ``[0, 1]``. ``cosine_score`` retains the source value and
    ``score_mapping`` makes the boundary transformation explicit. Chunk-level
    :class:`VectorSearchMatch` values remain unchanged raw cosine results.
    """

    cosine_score: float
    score_mapping: Literal["(cosine + 1) / 2"] = LEGACY_COSINE_SCORE_MAPPING


class PublicationChunkFactory(Protocol):
    """Adapt publications plus caller-owned page data into validated chunks."""

    def __call__(
        self,
        publications: Sequence[ResearchPublication],
    ) -> Sequence[PublicationChunk]:
        """Return chunks attributable only to the supplied publications."""


class VectorSearchAlgorithm:
    """Build and query a chunk-level vector index while preserving search inputs.

    The existing ``PublicationSearchAlgorithm`` ABC is deliberately unchanged
    and is not inherited here: its ``build_index`` accepts publications without
    page text, while this index requires already validated page-aware chunks;
    its result also cannot carry chunk/page provenance. ``search`` retains the
    existing query/filter/limit arguments, and ``search_publications`` provides
    an explicit, lossy projection to legacy ``SearchMatch`` values.

    Ranking quality depends entirely on the selected embedding provider. The
    deterministic fake provider has no semantic meaning, so successful tests of
    this class establish only contract correctness and deterministic behavior.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        *,
        chunking_version: str,
        index: VectorIndex | None = None,
    ) -> None:
        normalized_chunking_version = chunking_version.strip()
        if not normalized_chunking_version:
            raise ValueError("chunking_version must not be blank")
        self._embedding_provider = embedding_provider
        self._chunking_version = normalized_chunking_version
        self._index = index or InMemoryVectorIndex()

    @property
    def index(self) -> VectorIndex:
        """Return the injected index abstraction for persistence or inspection."""
        return self._index

    @property
    def manifest(self) -> VectorIndexManifest | None:
        """Return the current index manifest, if ``build_index`` has succeeded."""
        return self._index.manifest

    def build_index(self, chunks: Sequence[PublicationChunk]) -> VectorIndexManifest:
        """Build or replace the chunk index using the configured provider."""
        return self._index.build(
            chunks,
            self._embedding_provider,
            chunking_version=self._chunking_version,
        )

    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[VectorSearchMatch]:
        """Return provenance-rich chunk matches after fail-closed version checks."""
        if limit <= 0 or not query.strip():
            return []
        if allowed_publication_ids is not None and not allowed_publication_ids:
            return []
        manifest = self._compatible_manifest()
        try:
            result = self._embedding_provider.embed_query(query)
        except Exception as error:
            raise VectorQueryEmbeddingError("embedding provider failed for the query") from error
        observed_settings: dict[str, object] = {
            "embedding_model_id": result.embedding_model_id,
            "embedding_version": result.embedding_version,
            "dimension": result.dimension,
            "normalization": "l2" if result.normalized else "none",
        }
        expected_settings: dict[str, object] = {
            "embedding_model_id": manifest.embedding_model_id,
            "embedding_version": manifest.embedding_version,
            "dimension": manifest.dimension,
            "normalization": manifest.normalization,
        }
        for field_name, expected_value in expected_settings.items():
            if observed_settings[field_name] != expected_value:
                raise VectorQueryEmbeddingError(
                    f"query embedding {field_name} does not match the index manifest"
                )
        if result.failures:
            raise VectorQueryEmbeddingError("query embedding returned a failure")
        if len(result.vectors) != 1 or result.vectors[0].input_index != 0:
            raise VectorQueryEmbeddingError("query embedding must return exactly input index 0")
        vector = result.vectors[0]
        query_checksum = sha256(query.encode("utf-8", errors="surrogatepass")).hexdigest()
        if vector.input_checksum != query_checksum:
            raise VectorQueryEmbeddingError(
                "query embedding checksum does not match the exact query text"
            )
        try:
            return self._index.nearest(
                vector.values,
                allowed_publication_ids=allowed_publication_ids,
                limit=limit,
            )
        except VectorIndexError as error:
            raise VectorQueryEmbeddingError("query embedding vector is invalid") from error

    def search_publications(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[LegacyVectorSearchMatch]:
        """Project each publication's best chunk into the legacy result shape.

        This projection intentionally discards chunk/page provenance. Callers
        that cite evidence must use ``search`` and retain ``VectorSearchMatch``.
        The raw cosine is monotonically mapped from ``[-1, 1]`` to the legacy
        score contract's ``[0, 1]`` with ``(cosine + 1) / 2`` and retained as
        ``cosine_score`` on the returned subtype. Embeddings expose no matched
        lexical terms, so ``matched_terms`` is empty and the source field is
        reported as publication content.
        """
        if limit <= 0 or not query.strip():
            return []
        if allowed_publication_ids is not None and not allowed_publication_ids:
            return []
        manifest = self._compatible_manifest()
        chunk_matches = self.search(
            query,
            allowed_publication_ids,
            manifest.indexed_chunk_count,
        )
        publication_matches: list[LegacyVectorSearchMatch] = []
        seen_publication_ids: set[str] = set()
        for match in chunk_matches:
            if match.publication_id in seen_publication_ids:
                continue
            seen_publication_ids.add(match.publication_id)
            publication_matches.append(
                LegacyVectorSearchMatch(
                    publication_id=match.publication_id,
                    score=(match.score + 1.0) / 2.0,
                    matched_fields=(SearchField.CONTENT,),
                    matched_terms=(),
                    cosine_score=match.score,
                )
            )
            if len(publication_matches) == limit:
                break
        return publication_matches

    def _compatible_manifest(self) -> VectorIndexManifest:
        manifest = self._index.manifest
        if manifest is None:
            raise VectorIndexNotBuiltError("vector index has not been built")
        current_settings: dict[str, object] = {
            "embedding_model_id": self._embedding_provider.embedding_model_id,
            "embedding_version": self._embedding_provider.embedding_version,
            "dimension": self._embedding_provider.dimension,
            "normalization": "l2" if self._embedding_provider.normalized else "none",
            "chunking_version": self._chunking_version,
        }
        indexed_settings: dict[str, object] = {
            "embedding_model_id": manifest.embedding_model_id,
            "embedding_version": manifest.embedding_version,
            "dimension": manifest.dimension,
            "normalization": manifest.normalization,
            "chunking_version": manifest.chunking_version,
        }
        for field_name, indexed_value in indexed_settings.items():
            if current_settings[field_name] != indexed_value:
                raise VectorSearchConfigurationError(
                    f"{field_name} mismatch between query settings and vector index"
                )
        return manifest


class PublicationVectorSearchAdapter(PublicationSearchAlgorithm):
    """Expose vector retrieval through the legacy publication-level ABC.

    ``ResearchPublication`` does not contain page objects, so the adapter takes
    an explicit caller-owned ``PublicationChunkFactory`` instead of fabricating
    page provenance. The rich chunk path remains available on the wrapped
    ``VectorSearchAlgorithm``; this ABC view intentionally returns only each
    publication's best chunk projected to ``SearchMatch``.
    """

    def __init__(
        self,
        algorithm: VectorSearchAlgorithm,
        chunk_factory: PublicationChunkFactory,
    ) -> None:
        self._algorithm = algorithm
        self._chunk_factory = chunk_factory

    def build_index(self, publications: Sequence[ResearchPublication]) -> None:
        """Create validated chunks through the adapter and replace the vector index."""
        publication_ids = [publication.publication_id for publication in publications]
        if len(publication_ids) != len(set(publication_ids)):
            raise ValueError("publication_id must be unique while adapting a vector index")
        chunks = tuple(self._chunk_factory(publications))
        unexpected_publication_ids = sorted(
            {chunk.publication_id for chunk in chunks} - set(publication_ids)
        )
        if unexpected_publication_ids:
            raise ValueError(
                "chunk factory returned chunks outside the supplied publications: "
                + ", ".join(unexpected_publication_ids)
            )
        self._algorithm.build_index(chunks)

    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[SearchMatch]:
        """Return each publication's best vector chunk in the legacy result shape."""
        matches: list[SearchMatch] = list(
            self._algorithm.search_publications(
                query,
                allowed_publication_ids,
                limit,
            )
        )
        return matches
