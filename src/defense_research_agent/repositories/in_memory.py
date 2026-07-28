"""In-memory repository backed by a replaceable search algorithm."""

from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from unicodedata import normalize

from defense_research_agent.domain import (
    PublicationDistribution,
    PublicationSearchFilters,
    PublicationSearchResult,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.repositories.base import ResearchPublicationRepository
from defense_research_agent.repositories.publication_index import (
    parse_publication_index,
)
from defense_research_agent.search import (
    LocalLexicalSearchAlgorithm,
    PublicationSearchAlgorithm,
)


class InMemoryResearchPublicationRepository(ResearchPublicationRepository):
    """Keep normalized publications in memory and delegate ranking."""

    def __init__(
        self,
        publications: Sequence[ResearchPublication],
        search_algorithm: PublicationSearchAlgorithm | None = None,
    ) -> None:
        self._publications = tuple(
            sorted(publications, key=lambda publication: publication.publication_id)
        )
        self._by_id: dict[str, ResearchPublication] = {}
        for publication in self._publications:
            if publication.publication_id in self._by_id:
                raise ValueError(f"duplicate publication_id: {publication.publication_id}")
            self._by_id[publication.publication_id] = publication
        self._search_algorithm = search_algorithm or LocalLexicalSearchAlgorithm()
        self._search_algorithm.build_index(tuple(self._by_id.values()))

    @classmethod
    def from_jsonl(
        cls,
        path: Path,
        search_algorithm: PublicationSearchAlgorithm | None = None,
    ) -> "InMemoryResearchPublicationRepository":
        """Load normalized publications from a UTF-8 JSONL artifact."""
        publications = parse_publication_index(path.read_bytes())
        return cls(publications, search_algorithm)

    def search(
        self,
        query: str,
        filters: PublicationSearchFilters | None = None,
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        """Filter publications and rank title, abstract, keywords, and content."""
        if limit <= 0 or not query.strip():
            return []
        effective_filters = filters or PublicationSearchFilters()
        allowed_ids = {
            publication.publication_id
            for publication in self._by_id.values()
            if _matches_filters(publication, effective_filters)
        }
        matches = self._search_algorithm.search(query, allowed_ids, limit)
        return [
            PublicationSearchResult(
                publication=self._by_id[match.publication_id],
                score=match.score,
                matched_fields=list(match.matched_fields),
                matched_terms=list(match.matched_terms),
            )
            for match in matches
        ]

    def get_by_id(self, publication_id: str) -> ResearchPublication | None:
        """Return one publication without raising for an unknown ID."""
        return self._by_id.get(publication_id)

    def find_similar(
        self,
        title: str | None,
        abstract: str | None,
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        """Use the configured algorithm with title and abstract as the query."""
        query = " ".join(value.strip() for value in (title, abstract) if value and value.strip())
        return self.search(query, limit=limit)

    def get_recent_publications(
        self,
        limit: int = 10,
        publication_types: Sequence[PublicationType] | None = None,
    ) -> list[ResearchPublication]:
        """Sort by exact date or filename-year evidence, then stable ID."""
        if limit <= 0:
            return []
        allowed_types = set(publication_types or ())
        candidates = [
            publication
            for publication in self._by_id.values()
            if not allowed_types or publication.publication_type in allowed_types
        ]
        candidates.sort(
            key=lambda publication: (
                -_effective_date_ordinal(publication),
                publication.publication_id,
            )
        )
        return candidates[:limit]

    def get_publication_distribution(
        self,
        start_date: date | None,
        end_date: date | None,
    ) -> PublicationDistribution:
        """Aggregate using exact dates or year-only ingestion evidence."""
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must be on or before end_date")

        known_date_publications: list[tuple[ResearchPublication, int]] = []
        unknown_date_count = 0
        for publication in self._by_id.values():
            year = _effective_year(publication)
            if year is None:
                unknown_date_count += 1
                continue
            if _matches_date_range(publication, start_date, end_date):
                known_date_publications.append((publication, year))

        by_year = Counter(year for _, year in known_date_publications)
        by_publication_type = Counter(
            publication.publication_type.value for publication, _ in known_date_publications
        )
        return PublicationDistribution(
            total=len(known_date_publications),
            by_year=dict(sorted(by_year.items())),
            by_publication_type=dict(sorted(by_publication_type.items())),
            unknown_date_count=unknown_date_count,
        )

    def find_related_by_keywords(
        self,
        keywords: Sequence[str],
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        """Rank a deterministic de-duplicated keyword query."""
        normalized_keywords = tuple(
            dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip())
        )
        return self.search(" ".join(normalized_keywords), limit=limit)


def _matches_filters(
    publication: ResearchPublication,
    filters: PublicationSearchFilters,
) -> bool:
    if filters.publication_types and publication.publication_type not in filters.publication_types:
        return False
    if filters.authors and not _matches_authors(publication, filters.authors):
        return False
    return not (
        filters.start_date is not None or filters.end_date is not None
    ) or _matches_date_range(publication, filters.start_date, filters.end_date)


def _matches_authors(
    publication: ResearchPublication,
    requested_authors: Sequence[str],
) -> bool:
    publication_authors = tuple(_normalize_label(author) for author in publication.authors)
    return any(
        _normalize_label(requested_author) in publication_author
        for requested_author in requested_authors
        for publication_author in publication_authors
    )


def _matches_date_range(
    publication: ResearchPublication,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    if publication.publication_date is not None:
        if start_date is not None and publication.publication_date < start_date:
            return False
        return end_date is None or publication.publication_date <= end_date

    year = _filename_year(publication)
    if year is None:
        return False
    if start_date is not None and year < start_date.year:
        return False
    return end_date is None or year <= end_date.year


def _effective_year(publication: ResearchPublication) -> int | None:
    if publication.publication_date is not None:
        return publication.publication_date.year
    return _filename_year(publication)


def _effective_date_ordinal(publication: ResearchPublication) -> int:
    if publication.publication_date is not None:
        return publication.publication_date.toordinal()
    year = _filename_year(publication)
    return date(year, 1, 1).toordinal() if year is not None else -1


def _filename_year(publication: ResearchPublication) -> int | None:
    ingestion_metadata = publication.raw_metadata.get("_ingestion")
    if not isinstance(ingestion_metadata, dict):
        return None
    year = ingestion_metadata.get("filename_year")
    if isinstance(year, bool) or not isinstance(year, int):
        return None
    return year if 1 <= year <= 9999 else None


def _normalize_label(value: str) -> str:
    return normalize("NFC", value).strip().casefold()
