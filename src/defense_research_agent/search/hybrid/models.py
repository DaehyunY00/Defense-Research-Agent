"""Validated, deterministic publication-level hybrid search results."""

import math
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from defense_research_agent.domain.common import Checksum, DomainModel, EntityId, Label
from defense_research_agent.domain.publication import PublicationChunk
from defense_research_agent.domain.search import SearchField

RRF_FUSION_STRATEGY: Final[Literal["reciprocal_rank_fusion"]] = "reciprocal_rank_fusion"
RRF_FUSION_VERSION: Final[Literal["rrf-publication-v1"]] = "rrf-publication-v1"
HYBRID_FILTER_STAGE: Final[Literal["pre_fusion"]] = "pre_fusion"
HYBRID_TIE_BREAKER: Final[Literal["fusion-score-desc,publication-id-asc"]] = (
    "fusion-score-desc,publication-id-asc"
)
DEFAULT_RRF_K: Final = 60
DEFAULT_CANDIDATE_LIMIT_PER_SOURCE: Final = 100

type RawSearchScore = Annotated[float, Field(allow_inf_nan=False)]
type FusionScore = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
type CosineScore = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]


class HybridSearchStatus(StrEnum):
    """Whether both sources ran, vector failed open, or no search was needed."""

    FUSED = "fused"
    LEXICAL_ONLY_FALLBACK = "lexical_only_fallback"
    NOT_RUN = "not_run"


class HybridVectorStatus(StrEnum):
    """Stable vector-side execution outcome visible to callers."""

    SUCCEEDED = "succeeded"
    INDEX_NOT_BUILT = "index_not_built"
    SEARCH_FAILED = "search_failed"
    NOT_RUN = "not_run"


class HybridFailureCode(StrEnum):
    """Stable vector failure categories that activate lexical-only fallback."""

    VECTOR_INDEX_NOT_BUILT = "vector_index_not_built"
    VECTOR_SEARCH_FAILED = "vector_search_failed"


class HybridSearchFailure(DomainModel):
    """Visible reason why hybrid search used the lexical-only fallback path."""

    source: Literal["vector"] = "vector"
    code: HybridFailureCode
    message: Label


class HybridFusionTrace(DomainModel):
    """Versioned ranking contract recorded alongside every result list."""

    strategy: Literal["reciprocal_rank_fusion"] = RRF_FUSION_STRATEGY
    version: Literal["rrf-publication-v1"] = RRF_FUSION_VERSION
    rrf_k: PositiveInt = DEFAULT_RRF_K
    candidate_limit_per_source: PositiveInt = DEFAULT_CANDIDATE_LIMIT_PER_SOURCE
    filter_stage: Literal["pre_fusion"] = HYBRID_FILTER_STAGE
    tie_breaker: Literal["fusion-score-desc,publication-id-asc"] = HYBRID_TIE_BREAKER


class HybridVectorIndexTrace(DomainModel):
    """Vector coverage metadata copied from the index manifest."""

    manifest_version: Label
    content_address: Checksum
    input_chunk_count: NonNegativeInt
    indexed_chunk_count: NonNegativeInt
    skipped_chunk_count: NonNegativeInt

    @model_validator(mode="after")
    def chunk_counts_must_balance(self) -> "HybridVectorIndexTrace":
        """Keep partial-index coverage internally consistent."""
        if self.indexed_chunk_count + self.skipped_chunk_count != self.input_chunk_count:
            raise ValueError("indexed and skipped chunk counts must equal input chunk count")
        return self


class HybridSearchMatch(DomainModel):
    """One publication with complete rank-fusion and source-score explanation."""

    publication_id: EntityId
    rank: PositiveInt
    fusion_score: FusionScore
    lexical_score: RawSearchScore | None = None
    lexical_rank: PositiveInt | None = None
    lexical_matched_fields: list[SearchField] = Field(default_factory=list)
    lexical_matched_terms: list[str] = Field(default_factory=list)
    vector_score: CosineScore | None = None
    vector_rank: PositiveInt | None = None
    vector_chunk_rank: PositiveInt | None = None
    vector_chunk: PublicationChunk | None = None

    @model_validator(mode="after")
    def source_fields_must_be_complete(self) -> "HybridSearchMatch":
        """Require score, rank, and source evidence to appear as one unit."""
        has_lexical = self.lexical_score is not None
        if has_lexical != (self.lexical_rank is not None):
            raise ValueError("lexical score and rank must either both be set or both be absent")
        if not has_lexical and (self.lexical_matched_fields or self.lexical_matched_terms):
            raise ValueError("lexical match evidence requires a lexical score and rank")

        vector_values = (
            self.vector_score,
            self.vector_rank,
            self.vector_chunk_rank,
            self.vector_chunk,
        )
        has_vector = self.vector_score is not None
        if any(value is not None for value in vector_values) != all(
            value is not None for value in vector_values
        ):
            raise ValueError("vector score, ranks, and chunk must all be set or all be absent")
        if (
            has_vector
            and self.vector_chunk is not None
            and self.vector_chunk.publication_id != self.publication_id
        ):
            raise ValueError("vector chunk publication_id must match the hybrid publication")
        if not has_lexical and not has_vector:
            raise ValueError("hybrid match must come from at least one search source")
        return self


class HybridSearchResult(DomainModel):
    """Deterministic hybrid output including source health and candidate accounting."""

    status: HybridSearchStatus
    vector_status: HybridVectorStatus
    fusion: HybridFusionTrace
    vector_index: HybridVectorIndexTrace | None = None
    lexical_candidate_count: NonNegativeInt
    vector_chunk_candidate_count: NonNegativeInt
    vector_publication_candidate_count: NonNegativeInt
    fusion_candidate_count: NonNegativeInt
    matches: list[HybridSearchMatch] = Field(default_factory=list)
    failure: HybridSearchFailure | None = None

    @model_validator(mode="after")
    def execution_and_ranking_contract_must_hold(self) -> "HybridSearchResult":
        """Reject hidden fallback, invalid ranks, or fusion scores without provenance."""
        if self.status is HybridSearchStatus.FUSED:
            if self.vector_status is not HybridVectorStatus.SUCCEEDED:
                raise ValueError("fused search requires a successful vector search")
            if self.failure is not None:
                raise ValueError("fused search must not include a failure")
        elif self.status is HybridSearchStatus.LEXICAL_ONLY_FALLBACK:
            if self.vector_status not in {
                HybridVectorStatus.INDEX_NOT_BUILT,
                HybridVectorStatus.SEARCH_FAILED,
            }:
                raise ValueError("lexical-only fallback requires a vector failure status")
            if self.failure is None:
                raise ValueError("lexical-only fallback must expose its failure")
            if self.vector_chunk_candidate_count or self.vector_publication_candidate_count:
                raise ValueError("failed vector search cannot report vector candidates")
        else:
            if self.vector_status is not HybridVectorStatus.NOT_RUN:
                raise ValueError("not-run search requires a not-run vector status")
            if self.failure is not None or self.matches:
                raise ValueError("not-run search cannot contain a failure or matches")
            if any(
                (
                    self.lexical_candidate_count,
                    self.vector_chunk_candidate_count,
                    self.vector_publication_candidate_count,
                    self.fusion_candidate_count,
                )
            ):
                raise ValueError("not-run search cannot report candidates")

        if self.fusion_candidate_count < len(self.matches):
            raise ValueError("returned matches cannot exceed the fusion candidate count")
        publication_ids = [match.publication_id for match in self.matches]
        if len(publication_ids) != len(set(publication_ids)):
            raise ValueError("hybrid result cannot return one publication more than once")
        for expected_rank, match in enumerate(self.matches, start=1):
            if match.rank != expected_rank:
                raise ValueError("hybrid ranks must be contiguous and match result order")
            contributions = [
                1.0 / (self.fusion.rrf_k + source_rank)
                for source_rank in (match.lexical_rank, match.vector_rank)
                if source_rank is not None
            ]
            if match.fusion_score != math.fsum(contributions):
                raise ValueError("fusion_score must equal the recorded RRF rank contributions")
        return self
