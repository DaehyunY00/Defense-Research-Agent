"""Provider interface for recent external defense and security issues."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date

from defense_research_agent.domain import (
    ExternalIssueSearchResult,
    ExternalSearchError,
    ExternalSearchStatus,
    ExternalSource,
)


class ExternalIssueProviderError(RuntimeError):
    """Expected provider failure safe for conversion to a status model."""


class ExternalIssueProviderTimeout(ExternalIssueProviderError):
    """Expected provider timeout without an actual wait in fixture tests."""


class ExternalIssueSearchProvider(ABC):
    """Replaceable provider contract used by the issue collection workflow."""

    @abstractmethod
    def search_recent_issues(
        self,
        query: str,
        start_date: date | None,
        end_date: date | None,
        domains: Sequence[str],
        limit: int,
    ) -> list[ExternalSource]:
        """Return matching sources or raise an expected provider exception."""

    def search_recent_issues_with_status(
        self,
        query: str,
        start_date: date | None,
        end_date: date | None,
        domains: Sequence[str],
        limit: int,
    ) -> ExternalIssueSearchResult:
        """Adapt the list contract to explicit success, timeout, or failure state."""
        try:
            sources = self.search_recent_issues(
                query,
                start_date,
                end_date,
                domains,
                limit,
            )
        except ExternalIssueProviderTimeout:
            return ExternalIssueSearchResult(
                status=ExternalSearchStatus.TIMEOUT,
                errors=[
                    ExternalSearchError(
                        code="provider_timeout",
                        message="external issue provider timed out",
                        retryable=True,
                    )
                ],
                requested_limit=max(limit, 0),
                returned_count=0,
            )
        except ExternalIssueProviderError as error:
            return ExternalIssueSearchResult(
                status=ExternalSearchStatus.FAILURE,
                errors=[
                    ExternalSearchError(
                        code="provider_failure",
                        message=f"external issue provider failed ({type(error).__name__})",
                        retryable=False,
                    )
                ],
                requested_limit=max(limit, 0),
                returned_count=0,
            )

        return ExternalIssueSearchResult(
            status=ExternalSearchStatus.SUCCESS,
            sources=sources,
            requested_limit=max(limit, 0),
            returned_count=len(sources),
        )
