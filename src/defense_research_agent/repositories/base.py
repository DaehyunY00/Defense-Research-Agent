"""Repository contract for internal research publication retrieval."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date

from defense_research_agent.domain import (
    PublicationDistribution,
    PublicationSearchFilters,
    PublicationSearchResult,
    PublicationType,
    ResearchPublication,
)


class ResearchPublicationRepository(ABC):
    """Storage-independent interface used by research-topic discovery services."""

    @abstractmethod
    def search(
        self,
        query: str,
        filters: PublicationSearchFilters | None = None,
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        """Search indexed publication fields with optional filters."""

    @abstractmethod
    def get_by_id(self, publication_id: str) -> ResearchPublication | None:
        """Return one publication or ``None`` when it is absent."""

    @abstractmethod
    def find_similar(
        self,
        title: str | None,
        abstract: str | None,
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        """Find publications lexically similar to supplied bibliography text."""

    @abstractmethod
    def get_recent_publications(
        self,
        limit: int = 10,
        publication_types: Sequence[PublicationType] | None = None,
    ) -> list[ResearchPublication]:
        """Return publications ordered by best available date evidence."""

    @abstractmethod
    def get_publication_distribution(
        self,
        start_date: date | None,
        end_date: date | None,
    ) -> PublicationDistribution:
        """Aggregate known-date publications by year and type."""

    @abstractmethod
    def find_related_by_keywords(
        self,
        keywords: Sequence[str],
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        """Find publications related to one or more keywords."""
