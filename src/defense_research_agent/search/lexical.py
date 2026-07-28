"""Dependency-free deterministic lexical ranking with Korean substring support."""

import math
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from unicodedata import normalize

from defense_research_agent.domain import ResearchPublication, SearchField
from defense_research_agent.search.base import PublicationSearchAlgorithm, SearchMatch

_TOKEN_PATTERN = re.compile(r"[0-9a-z가-힣]+")
_SEPARATOR_PATTERN = re.compile(r"[^0-9a-z가-힣]+")
_SEARCH_FIELDS = (
    SearchField.TITLE,
    SearchField.ABSTRACT,
    SearchField.KEYWORDS,
    SearchField.CONTENT,
)
_FIELD_WEIGHTS = {
    SearchField.TITLE: 4.0,
    SearchField.ABSTRACT: 3.0,
    SearchField.KEYWORDS: 3.5,
    SearchField.CONTENT: 1.0,
}


@dataclass(frozen=True, slots=True)
class _IndexedPublication:
    publication_id: str
    fields: dict[SearchField, str]


class LocalLexicalSearchAlgorithm(PublicationSearchAlgorithm):
    """Rank exact normalized query terms with TF-IDF-inspired field weights."""

    def __init__(self) -> None:
        self._documents: tuple[_IndexedPublication, ...] = ()

    def build_index(self, publications: Sequence[ResearchPublication]) -> None:
        """Store normalized searchable fields in deterministic publication-ID order."""
        self._documents = tuple(
            _IndexedPublication(
                publication_id=publication.publication_id,
                fields={
                    SearchField.TITLE: _normalize_search_text(publication.title or ""),
                    SearchField.ABSTRACT: _normalize_search_text(publication.abstract or ""),
                    SearchField.KEYWORDS: _normalize_search_text(" ".join(publication.keywords)),
                    SearchField.CONTENT: _normalize_search_text(publication.content or ""),
                },
            )
            for publication in sorted(
                publications,
                key=lambda item: item.publication_id,
            )
        )

    def search(
        self,
        query: str,
        allowed_publication_ids: Collection[str] | None,
        limit: int,
    ) -> list[SearchMatch]:
        """Rank term occurrences and return stable ties by publication ID."""
        if limit <= 0:
            return []
        terms = _query_terms(query)
        if not terms or not self._documents:
            return []

        allowed_ids = set(allowed_publication_ids) if allowed_publication_ids is not None else None
        candidates = tuple(
            document
            for document in self._documents
            if allowed_ids is None or document.publication_id in allowed_ids
        )
        if not candidates:
            return []

        document_frequencies = {
            term: sum(
                any(term in field_text for field_text in document.fields.values())
                for document in self._documents
            )
            for term in terms
        }
        total_documents = len(self._documents)
        matches: list[SearchMatch] = []
        for document in candidates:
            score = 0.0
            matched_fields: list[SearchField] = []
            matched_terms: set[str] = set()
            for field in _SEARCH_FIELDS:
                field_text = document.fields[field]
                field_matched = False
                for term in terms:
                    term_frequency = field_text.count(term)
                    if term_frequency == 0:
                        continue
                    inverse_document_frequency = (
                        math.log((total_documents + 1) / (document_frequencies[term] + 1)) + 1.0
                    )
                    score += (
                        _FIELD_WEIGHTS[field]
                        * (1.0 + math.log(term_frequency))
                        * inverse_document_frequency
                    )
                    field_matched = True
                    matched_terms.add(term)
                if field_matched:
                    matched_fields.append(field)

            if score > 0.0:
                matches.append(
                    SearchMatch(
                        publication_id=document.publication_id,
                        score=round(score, 8),
                        matched_fields=tuple(matched_fields),
                        matched_terms=tuple(term for term in terms if term in matched_terms),
                    )
                )

        matches.sort(key=lambda match: (-match.score, match.publication_id))
        return matches[:limit]


def _query_terms(query: str) -> tuple[str, ...]:
    normalized_query = normalize("NFC", query).casefold()
    return tuple(dict.fromkeys(_TOKEN_PATTERN.findall(normalized_query)))


def _normalize_search_text(value: str) -> str:
    normalized_value = normalize("NFC", value).casefold()
    return _SEPARATOR_PATTERN.sub(" ", normalized_value).strip()
