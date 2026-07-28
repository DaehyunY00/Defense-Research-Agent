"""Replaceable ranking interface for publication search."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from defense_research_agent.domain import ResearchPublication, SearchField


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """Algorithm-level result independent from repository storage."""

    publication_id: str
    score: float
    matched_fields: tuple[SearchField, ...]
    matched_terms: tuple[str, ...]


class PublicationSearchAlgorithm(ABC):
    """Interface that local lexical or future vector search can implement."""

    @abstractmethod
    def build_index(self, publications: Sequence[ResearchPublication]) -> None:
        """Build or replace the algorithm's index."""

    @abstractmethod
    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[SearchMatch]:
        """Rank publications, optionally restricted to an allowed ID set."""
