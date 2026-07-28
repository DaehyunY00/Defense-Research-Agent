"""Fixture-only external issue provider with no network capability."""

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import cast
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from defense_research_agent.domain import (
    ExternalIssueSearchResult,
    ExternalSearchError,
    ExternalSearchStatus,
    ExternalSource,
)
from defense_research_agent.issues.base import (
    ExternalIssueProviderError,
    ExternalIssueProviderTimeout,
    ExternalIssueSearchProvider,
)
from defense_research_agent.issues.priority import external_source_priority


class _FixtureScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ExternalSearchStatus
    message: str
    retryable: bool = False


class _FixtureDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[dict[str, JsonValue]]
    scenarios: dict[str, _FixtureScenario] = Field(default_factory=dict)


class MockExternalIssueSearchProvider(ExternalIssueSearchProvider):
    """Search a checked-in JSON fixture and simulate failures deterministically."""

    def __init__(self, fixture_path: Path) -> None:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self._fixture = _FixtureDocument.model_validate(payload)

    def search_recent_issues(
        self,
        query: str,
        start_date: date | None,
        end_date: date | None,
        domains: Sequence[str],
        limit: int,
    ) -> list[ExternalSource]:
        """Return valid fixture sources and raise only for total provider failures."""
        result = self.search_recent_issues_with_status(
            query,
            start_date,
            end_date,
            domains,
            limit,
        )
        if result.status is ExternalSearchStatus.TIMEOUT:
            raise ExternalIssueProviderTimeout(result.errors[0].message)
        if result.status is ExternalSearchStatus.FAILURE:
            raise ExternalIssueProviderError(result.errors[0].message)
        return result.sources

    def search_recent_issues_with_status(
        self,
        query: str,
        start_date: date | None,
        end_date: date | None,
        domains: Sequence[str],
        limit: int,
    ) -> ExternalIssueSearchResult:
        """Return item-level validation errors alongside successfully parsed items."""
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        if limit < 0:
            raise ValueError("limit must be zero or greater")

        scenario = self._fixture.scenarios.get(query)
        if scenario is not None:
            return ExternalIssueSearchResult(
                status=scenario.status,
                errors=[
                    ExternalSearchError(
                        code=(
                            "provider_timeout"
                            if scenario.status is ExternalSearchStatus.TIMEOUT
                            else "provider_failure"
                        ),
                        message=scenario.message,
                        retryable=scenario.retryable,
                    )
                ],
                requested_limit=limit,
                returned_count=0,
            )

        sources: list[ExternalSource] = []
        errors: list[ExternalSearchError] = []
        for raw_source in self._fixture.sources:
            if not _raw_matches_query_and_domains(raw_source, query, domains):
                continue
            try:
                source = ExternalSource.model_validate(raw_source)
            except ValidationError:
                errors.append(
                    ExternalSearchError(
                        code="source_validation_error",
                        message="fixture source failed ExternalSource validation",
                        source_reference=_raw_source_reference(raw_source),
                    )
                )
                continue
            if not _matches_date_range(source, start_date, end_date):
                continue
            sources.append(source)

        sources.sort(key=external_source_priority)
        limited_sources = sources[:limit]
        status = (
            ExternalSearchStatus.PARTIAL
            if errors and limited_sources
            else ExternalSearchStatus.FAILURE
            if errors
            else ExternalSearchStatus.SUCCESS
        )
        return ExternalIssueSearchResult(
            status=status,
            sources=limited_sources,
            errors=errors,
            requested_limit=limit,
            returned_count=len(limited_sources),
        )


def _raw_matches_query_and_domains(
    raw_source: dict[str, JsonValue],
    query: str,
    domains: Sequence[str],
) -> bool:
    normalized_query_terms = _normalized_terms(query)
    raw_text_parts = (
        raw_source.get("title"),
        raw_source.get("publisher"),
        raw_source.get("snippet"),
    )
    haystack = _normalize_text(" ".join(part for part in raw_text_parts if isinstance(part, str)))
    if normalized_query_terms and not all(term in haystack for term in normalized_query_terms):
        return False

    if not domains:
        return True
    raw_domains = raw_source.get("policy_domains")
    if not isinstance(raw_domains, list):
        return False
    normalized_source_domains = {
        _normalize_text(domain)
        for domain in cast(list[object], raw_domains)
        if isinstance(domain, str)
    }
    return bool(
        normalized_source_domains
        & {_normalize_text(domain) for domain in domains if domain.strip()}
    )


def _matches_date_range(
    source: ExternalSource,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    if start_date is None and end_date is None:
        return True
    if source.publication_date is None:
        return False
    if start_date is not None and source.publication_date < start_date:
        return False
    return end_date is None or source.publication_date <= end_date


def _raw_source_reference(raw_source: dict[str, JsonValue]) -> str | None:
    source_id = raw_source.get("source_id")
    return source_id if isinstance(source_id, str) else None


def _normalized_terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_normalize_text(value).split()))


def _normalize_text(value: str) -> str:
    return " ".join(normalize("NFC", value).strip().casefold().split())
