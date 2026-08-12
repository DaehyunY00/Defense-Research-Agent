"""Reciprocal-rank fusion over publication lexical and chunk vector retrieval."""

import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from defense_research_agent.search.base import SearchMatch
from defense_research_agent.search.hybrid.models import (
    DEFAULT_CANDIDATE_LIMIT_PER_SOURCE,
    DEFAULT_RRF_K,
    HybridFailureCode,
    HybridFusionTrace,
    HybridSearchFailure,
    HybridSearchMatch,
    HybridSearchResult,
    HybridSearchStatus,
    HybridVectorCoverageStatus,
    HybridVectorIndexTrace,
    HybridVectorStatus,
)
from defense_research_agent.search.vector.index import VectorIndexNotBuiltError
from defense_research_agent.search.vector.models import VectorIndexManifest, VectorSearchMatch


class LexicalPublicationSearch(Protocol):
    """Structural boundary for an injected publication-level lexical search."""

    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[SearchMatch]:
        """Return publication matches in deterministic rank order."""


class ChunkVectorSearch(Protocol):
    """Structural boundary for an injected provenance-rich vector search."""

    @property
    def manifest(self) -> VectorIndexManifest | None:
        """Return current vector build metadata, or ``None`` before build."""

    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[VectorSearchMatch]:
        """Return chunk matches in deterministic vector rank order."""


class HybridSearchContractError(ValueError):
    """Raised when an injected search violates the hybrid input contract."""


@dataclass(frozen=True, slots=True)
class _LexicalCandidate:
    match: SearchMatch
    rank: int


@dataclass(frozen=True, slots=True)
class _VectorCandidate:
    match: VectorSearchMatch
    rank: int
    chunk_rank: int


@dataclass(frozen=True, slots=True)
class _VectorProjection:
    candidates: dict[str, _VectorCandidate]
    observed_publication_ranks: dict[str, int]

    @property
    def truncated(self) -> bool:
        """Return whether at least one observed publication fell outside the source depth."""
        return len(self.observed_publication_ranks) > len(self.candidates)


@dataclass(frozen=True, slots=True)
class _UnrankedHybridMatch:
    publication_id: str
    fusion_score: float
    lexical: _LexicalCandidate | None
    vector: _VectorCandidate | None
    vector_coverage_status: HybridVectorCoverageStatus
    vector_observed_publication_rank: int | None
    vector_skipped_chunk_count: int


class HybridSearchAlgorithm:
    """Fuse source ranks without comparing their incompatible raw scores.

    Lexical results already use publications while vector results use chunks.
    This boundary projects vector results to publications by retaining the first
    (therefore best-ranked) chunk per publication. It assigns contiguous vector
    publication ranks after projection and preserves both that rank and the
    original chunk rank in every match.

    ``allowed_publication_ids`` is snapshotted and passed to both sources before
    source ranking. A missing or failed vector index fails open to the lexical
    ranking and is made visible in the returned status and failure fields.
    Lexical exceptions propagate because lexical retrieval is the required
    deterministic baseline rather than an optional enhancement.
    """

    def __init__(
        self,
        lexical_search: LexicalPublicationSearch,
        vector_search: ChunkVectorSearch,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        candidate_limit_per_source: int = DEFAULT_CANDIDATE_LIMIT_PER_SOURCE,
    ) -> None:
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k <= 0:
            raise ValueError("rrf_k must be a positive integer")
        if (
            isinstance(candidate_limit_per_source, bool)
            or not isinstance(candidate_limit_per_source, int)
            or candidate_limit_per_source <= 0
        ):
            raise ValueError("candidate_limit_per_source must be a positive integer")
        self._lexical_search = lexical_search
        self._vector_search = vector_search
        self._rrf_k = rrf_k
        self._candidate_limit_per_source = candidate_limit_per_source

    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> HybridSearchResult:
        """Return a deterministic publication ranking with complete fusion trace."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an int")
        source_limit = max(self._candidate_limit_per_source, max(limit, 1))
        fusion = HybridFusionTrace(
            rrf_k=self._rrf_k,
            candidate_limit_per_source=source_limit,
        )
        allowed_ids = self._allowed_ids_snapshot(allowed_publication_ids)
        manifest = self._vector_search.manifest
        vector_index = self._vector_index_trace(manifest)
        if limit <= 0 or not query.strip() or allowed_ids == ():
            return HybridSearchResult(
                status=HybridSearchStatus.NOT_RUN,
                vector_status=HybridVectorStatus.NOT_RUN,
                fusion=fusion,
                vector_index=vector_index,
                lexical_candidate_count=0,
                lexical_candidates_truncated=False,
                vector_chunk_candidate_count=0,
                vector_publication_candidate_count=0,
                vector_publication_candidates_truncated=False,
                fusion_candidate_count=0,
            )

        lexical_matches = self._lexical_search.search(query, allowed_ids, source_limit + 1)
        lexical_candidates, lexical_candidates_truncated = self._prepare_lexical_candidates(
            lexical_matches,
            allowed_ids=allowed_ids,
            source_limit=source_limit,
        )

        vector_status = HybridVectorStatus.SUCCEEDED
        failure: HybridSearchFailure | None = None
        vector_matches: list[VectorSearchMatch] = []
        vector_projection = _VectorProjection(candidates={}, observed_publication_ranks={})
        if manifest is None:
            vector_status = HybridVectorStatus.INDEX_NOT_BUILT
            failure = HybridSearchFailure(
                code=HybridFailureCode.VECTOR_INDEX_NOT_BUILT,
                message="vector index is not built; lexical-only ranking returned",
            )
        else:
            try:
                vector_matches = self._vector_search.search(
                    query,
                    allowed_ids,
                    max(manifest.indexed_chunk_count, 1),
                )
            except VectorIndexNotBuiltError:
                vector_status = HybridVectorStatus.INDEX_NOT_BUILT
                failure = HybridSearchFailure(
                    code=HybridFailureCode.VECTOR_INDEX_NOT_BUILT,
                    message="vector index is not built; lexical-only ranking returned",
                )
            except Exception:
                vector_status = HybridVectorStatus.SEARCH_FAILED
                failure = HybridSearchFailure(
                    code=HybridFailureCode.VECTOR_SEARCH_FAILED,
                    message="vector search failed; lexical-only ranking returned",
                )
            else:
                vector_projection = self._project_vector_candidates(
                    vector_matches,
                    allowed_ids=allowed_ids,
                    source_limit=source_limit,
                )

        ranked_matches, fusion_candidate_count = self._fuse(
            lexical_candidates,
            vector_projection,
            vector_status=vector_status,
            skipped_chunk_counts=self._skipped_chunk_counts(manifest),
            limit=limit,
        )
        status = (
            HybridSearchStatus.FUSED
            if vector_status is HybridVectorStatus.SUCCEEDED
            else HybridSearchStatus.LEXICAL_ONLY_FALLBACK
        )
        return HybridSearchResult(
            status=status,
            vector_status=vector_status,
            fusion=fusion,
            vector_index=vector_index,
            lexical_candidate_count=len(lexical_candidates),
            lexical_candidates_truncated=lexical_candidates_truncated,
            vector_chunk_candidate_count=len(vector_matches),
            vector_publication_candidate_count=len(vector_projection.candidates),
            vector_publication_candidates_truncated=vector_projection.truncated,
            fusion_candidate_count=fusion_candidate_count,
            matches=ranked_matches,
            failure=failure,
        )

    @staticmethod
    def _allowed_ids_snapshot(
        allowed_publication_ids: Collection[str] | None,
    ) -> tuple[str, ...] | None:
        if allowed_publication_ids is None:
            return None
        return tuple(sorted(set(allowed_publication_ids)))

    @staticmethod
    def _vector_index_trace(
        manifest: VectorIndexManifest | None,
    ) -> HybridVectorIndexTrace | None:
        if manifest is None:
            return None
        return HybridVectorIndexTrace(
            manifest_version=manifest.manifest_version,
            content_address=manifest.content_address,
            input_chunk_count=manifest.input_chunk_count,
            indexed_chunk_count=manifest.indexed_chunk_count,
            skipped_chunk_count=manifest.skipped_chunk_count,
        )

    @staticmethod
    def _prepare_lexical_candidates(
        matches: list[SearchMatch],
        *,
        allowed_ids: tuple[str, ...] | None,
        source_limit: int,
    ) -> tuple[dict[str, _LexicalCandidate], bool]:
        if len(matches) > source_limit + 1:
            raise HybridSearchContractError("lexical search returned more than its requested limit")
        allowed_set = None if allowed_ids is None else set(allowed_ids)
        candidates: dict[str, _LexicalCandidate] = {}
        seen_publication_ids: set[str] = set()
        for rank, match in enumerate(matches, start=1):
            if not math.isfinite(match.score):
                raise HybridSearchContractError("lexical search returned a non-finite score")
            if match.publication_id in seen_publication_ids:
                raise HybridSearchContractError(
                    "lexical search returned one publication more than once"
                )
            seen_publication_ids.add(match.publication_id)
            if allowed_set is not None and match.publication_id not in allowed_set:
                raise HybridSearchContractError("lexical search returned a filtered publication")
            if rank > source_limit:
                continue
            candidates[match.publication_id] = _LexicalCandidate(match=match, rank=rank)
        return candidates, len(matches) > source_limit

    @staticmethod
    def _project_vector_candidates(
        matches: list[VectorSearchMatch],
        *,
        allowed_ids: tuple[str, ...] | None,
        source_limit: int,
    ) -> _VectorProjection:
        allowed_set = None if allowed_ids is None else set(allowed_ids)
        seen_chunk_ids: set[str] = set()
        candidates: dict[str, _VectorCandidate] = {}
        observed_publication_ranks: dict[str, int] = {}
        for chunk_rank, match in enumerate(matches, start=1):
            if match.chunk_id in seen_chunk_ids:
                raise HybridSearchContractError("vector search returned one chunk more than once")
            seen_chunk_ids.add(match.chunk_id)
            if allowed_set is not None and match.publication_id not in allowed_set:
                raise HybridSearchContractError("vector search returned a filtered publication")
            if match.publication_id in observed_publication_ranks:
                continue
            publication_rank = len(observed_publication_ranks) + 1
            observed_publication_ranks[match.publication_id] = publication_rank
            if publication_rank > source_limit:
                continue
            candidates[match.publication_id] = _VectorCandidate(
                match=match.model_copy(deep=True),
                rank=publication_rank,
                chunk_rank=chunk_rank,
            )
        return _VectorProjection(
            candidates=candidates,
            observed_publication_ranks=observed_publication_ranks,
        )

    @staticmethod
    def _skipped_chunk_counts(manifest: VectorIndexManifest | None) -> dict[str, int]:
        counts: dict[str, int] = {}
        if manifest is None:
            return counts
        for skipped_chunk in manifest.skipped_chunks:
            counts[skipped_chunk.publication_id] = counts.get(skipped_chunk.publication_id, 0) + 1
        return counts

    def _fuse(
        self,
        lexical_candidates: dict[str, _LexicalCandidate],
        vector_projection: _VectorProjection,
        *,
        vector_status: HybridVectorStatus,
        skipped_chunk_counts: dict[str, int],
        limit: int,
    ) -> tuple[list[HybridSearchMatch], int]:
        vector_candidates = vector_projection.candidates
        publication_ids = sorted(lexical_candidates.keys() | vector_candidates.keys())
        unranked: list[_UnrankedHybridMatch] = []
        for publication_id in publication_ids:
            lexical = lexical_candidates.get(publication_id)
            vector = vector_candidates.get(publication_id)
            observed_vector_rank = vector_projection.observed_publication_ranks.get(publication_id)
            if vector_status is not HybridVectorStatus.SUCCEEDED:
                vector_coverage_status = HybridVectorCoverageStatus.UNAVAILABLE
            elif vector is not None:
                vector_coverage_status = HybridVectorCoverageStatus.RANKED
            elif observed_vector_rank is not None:
                vector_coverage_status = HybridVectorCoverageStatus.SOURCE_DEPTH_TRUNCATED
            else:
                vector_coverage_status = HybridVectorCoverageStatus.NOT_INDEXED
            source_ranks = (
                lexical.rank if lexical is not None else None,
                vector.rank if vector is not None else None,
            )
            fusion_score = math.fsum(
                1.0 / (self._rrf_k + rank) for rank in source_ranks if rank is not None
            )
            unranked.append(
                _UnrankedHybridMatch(
                    publication_id=publication_id,
                    fusion_score=fusion_score,
                    lexical=lexical,
                    vector=vector,
                    vector_coverage_status=vector_coverage_status,
                    vector_observed_publication_rank=observed_vector_rank,
                    vector_skipped_chunk_count=skipped_chunk_counts.get(publication_id, 0),
                )
            )
        unranked.sort(key=lambda item: (-item.fusion_score, item.publication_id))

        matches = [
            self._result_match(item, rank=rank)
            for rank, item in enumerate(unranked[:limit], start=1)
        ]
        return matches, len(unranked)

    @staticmethod
    def _result_match(item: _UnrankedHybridMatch, *, rank: int) -> HybridSearchMatch:
        lexical_match = item.lexical.match if item.lexical is not None else None
        vector_match = item.vector.match if item.vector is not None else None
        return HybridSearchMatch(
            publication_id=item.publication_id,
            rank=rank,
            fusion_score=item.fusion_score,
            lexical_score=lexical_match.score if lexical_match is not None else None,
            lexical_rank=item.lexical.rank if item.lexical is not None else None,
            lexical_matched_fields=(
                list(lexical_match.matched_fields) if lexical_match is not None else []
            ),
            lexical_matched_terms=(
                list(lexical_match.matched_terms) if lexical_match is not None else []
            ),
            vector_score=vector_match.score if vector_match is not None else None,
            vector_rank=item.vector.rank if item.vector is not None else None,
            vector_chunk_rank=item.vector.chunk_rank if item.vector is not None else None,
            vector_chunk=(
                vector_match.chunk.model_copy(deep=True) if vector_match is not None else None
            ),
            vector_coverage_status=item.vector_coverage_status,
            vector_observed_publication_rank=item.vector_observed_publication_rank,
            vector_skipped_chunk_count=item.vector_skipped_chunk_count,
        )
