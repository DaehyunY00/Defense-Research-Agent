"""Tests for the offline fixture external issue provider."""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from defense_research_agent.domain import (
    ExternalSearchStatus,
    ExternalSource,
    ExternalSourceType,
)
from defense_research_agent.issues import (
    ExternalIssueProviderError,
    ExternalIssueProviderTimeout,
    ExternalIssueSearchProvider,
    MockExternalIssueSearchProvider,
)

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "external_issues.json"


@pytest.fixture
def provider() -> MockExternalIssueSearchProvider:
    return MockExternalIssueSearchProvider(FIXTURE_PATH)


class _SecretLeakingProvider(ExternalIssueSearchProvider):
    def search_recent_issues(
        self,
        query: str,
        start_date: date | None,
        end_date: date | None,
        domains: Sequence[str],
        limit: int,
    ) -> list[ExternalSource]:
        del query, start_date, end_date, domains, limit
        raise ExternalIssueProviderError("provider saw sk-ant-must-not-leak")


def test_mock_provider_implements_interface_and_searches_fixture(
    provider: MockExternalIssueSearchProvider,
) -> None:
    assert isinstance(provider, ExternalIssueSearchProvider)

    sources = provider.search_recent_issues(
        "첨단무기 획득사업",
        date(2026, 1, 1),
        date(2026, 12, 31),
        ["국방획득"],
        10,
    )

    assert [source.source_id for source in sources] == ["ext:audit:acquisition-review"]
    assert sources[0].source_type is ExternalSourceType.LEGISLATIVE_OVERSIGHT
    assert sources[0].publisher == "감사원"


def test_base_provider_status_sanitizes_provider_failure_details() -> None:
    result = _SecretLeakingProvider().search_recent_issues_with_status(
        "query",
        None,
        None,
        [],
        10,
    )

    assert result.status is ExternalSearchStatus.FAILURE
    assert "ExternalIssueProviderError" in result.errors[0].message
    assert "sk-ant-must-not-leak" not in result.model_dump_json()


def test_partial_success_keeps_valid_sources_and_reports_invalid_url(
    provider: MockExternalIssueSearchProvider,
) -> None:
    result = provider.search_recent_issues_with_status(
        "국방 AI",
        date(2026, 6, 1),
        date(2026, 7, 31),
        ["국방인공지능"],
        20,
    )

    assert result.status is ExternalSearchStatus.PARTIAL
    assert result.returned_count > 0
    assert result.errors[0].code == "source_validation_error"
    assert result.errors[0].source_reference == "ext:invalid:bad-url"
    assert all(source.source_id != "ext:invalid:bad-url" for source in result.sources)


def test_official_source_is_returned_first(
    provider: MockExternalIssueSearchProvider,
) -> None:
    result = provider.search_recent_issues_with_status("", None, None, [], 20)

    assert result.sources[0].source_id == "ext:gov:ai-workforce-policy"
    assert result.sources[0].publication_date == date(2026, 7, 1)


def test_missing_date_is_safe_and_excluded_only_when_date_filter_is_used(
    provider: MockExternalIssueSearchProvider,
) -> None:
    without_date_filter = provider.search_recent_issues(
        "공급망",
        None,
        None,
        [],
        10,
    )
    with_date_filter = provider.search_recent_issues(
        "공급망",
        date(2026, 1, 1),
        date(2026, 12, 31),
        [],
        10,
    )

    assert without_date_filter[0].publication_date is None
    assert with_date_filter == []


def test_timeout_and_failure_scenarios_have_explicit_status(
    provider: MockExternalIssueSearchProvider,
) -> None:
    timeout = provider.search_recent_issues_with_status(
        "simulate-timeout",
        None,
        None,
        [],
        10,
    )
    failure = provider.search_recent_issues_with_status(
        "simulate-failure",
        None,
        None,
        [],
        10,
    )

    assert timeout.status is ExternalSearchStatus.TIMEOUT
    assert timeout.errors[0].retryable is True
    assert failure.status is ExternalSearchStatus.FAILURE
    assert failure.errors[0].retryable is False

    with pytest.raises(ExternalIssueProviderTimeout, match="timed out"):
        provider.search_recent_issues("simulate-timeout", None, None, [], 10)
    with pytest.raises(ExternalIssueProviderError, match="failed"):
        provider.search_recent_issues("simulate-failure", None, None, [], 10)


def test_fixture_covers_required_source_classes(
    provider: MockExternalIssueSearchProvider,
) -> None:
    result = provider.search_recent_issues_with_status("", None, None, [], 20)

    assert {source.source_type for source in result.sources} == {
        ExternalSourceType.GOVERNMENT_POLICY,
        ExternalSourceType.DEFENSE_PRESS_RELEASE,
        ExternalSourceType.LEGISLATIVE_OVERSIGHT,
        ExternalSourceType.THINK_TANK_REPORT,
        ExternalSourceType.NEWS_ARTICLE,
    }
