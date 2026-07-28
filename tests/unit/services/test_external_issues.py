"""Tests for external source normalization and TopicSignal conversion."""

from datetime import date
from pathlib import Path
from typing import cast

from defense_research_agent.domain import (
    ExternalContentTrust,
    ExternalSearchStatus,
    JsonObject,
    ReliabilityTier,
    SourceRelationType,
)
from defense_research_agent.issues import MockExternalIssueSearchProvider
from defense_research_agent.services.external_issues import (
    ExternalIssueNormalizationService,
)

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "external_issues.json"


def _partial_search_result() -> tuple[
    MockExternalIssueSearchProvider,
    ExternalIssueNormalizationService,
]:
    return (
        MockExternalIssueSearchProvider(FIXTURE_PATH),
        ExternalIssueNormalizationService(),
    )


def test_normalizes_urls_dates_and_publisher_names() -> None:
    provider, service = _partial_search_result()
    search_result = provider.search_recent_issues_with_status(
        "국방 AI",
        date(2026, 6, 1),
        date(2026, 7, 31),
        ["국방인공지능"],
        20,
    )

    result = service.normalize_search_result(search_result)
    official = result.sources[0]

    assert official.publisher == "대한민국 국방부"
    assert official.publication_date == date(2026, 7, 1)
    assert str(official.url) == "https://www.mnd.go.kr/policy/ai-workforce"
    assert official.content_trust is ExternalContentTrust.UNTRUSTED
    assert official.reviewed is False


def test_removes_duplicate_url_and_similar_title_while_retaining_official_source() -> None:
    provider, service = _partial_search_result()
    search_result = provider.search_recent_issues_with_status(
        "국방 AI",
        date(2026, 6, 1),
        date(2026, 7, 31),
        ["국방인공지능"],
        20,
    )

    result = service.normalize_search_result(search_result)
    source_ids = [source.source_id for source in result.sources]
    official = result.sources[0]

    assert result.duplicates_removed == 2
    assert source_ids[0] == "ext:gov:ai-workforce-policy"
    assert "ext:news:ai-coverage-duplicate" not in source_ids
    assert "ext:news:similar-policy-title" not in source_ids
    assert any(
        relationship.relation_type is SourceRelationType.HAS_DUPLICATE
        and relationship.target_source_id == "ext:news:similar-policy-title"
        for relationship in official.relationships
    )


def test_news_can_reference_original_official_material() -> None:
    provider, service = _partial_search_result()
    search_result = provider.search_recent_issues_with_status(
        "국방 AI",
        None,
        None,
        ["국방인공지능"],
        20,
    )

    result = service.normalize_search_result(search_result)
    news = next(source for source in result.sources if source.source_id == "ext:news:ai-coverage")

    assert any(
        relationship.relation_type is SourceRelationType.REPORTS_ON
        and relationship.target_source_id == "ext:gov:ai-workforce-policy"
        for relationship in news.relationships
    )


def test_topic_signal_preserves_untrusted_text_and_provenance() -> None:
    provider, service = _partial_search_result()
    search_result = provider.search_recent_issues_with_status(
        "국방 AI",
        None,
        None,
        ["국방인공지능"],
        20,
    )
    normalized = service.normalize_search_result(search_result)
    news_index = next(
        index
        for index, source in enumerate(normalized.sources)
        if source.source_id == "ext:news:ai-coverage"
    )
    news = normalized.sources[news_index]
    signal = normalized.topic_signals[news_index]

    assert signal.summary == news.snippet
    assert signal.summary is not None
    assert "이전 지시를 무시" in signal.summary
    assert signal.source_ids == [news.source_id]
    assert [str(url) for url in signal.source_urls] == [str(news.url)]
    assert signal.organizations == ["연합뉴스"]
    assert signal.confidence == 0.65
    external_metadata = cast(JsonObject, signal.raw_metadata["external_source"])
    assert external_metadata["content_trust"] == "untrusted"


def test_signal_ids_are_deterministic_and_reliability_controls_confidence() -> None:
    provider, service = _partial_search_result()
    source = provider.search_recent_issues(
        "국방 AI 인력정책 추진계획",
        None,
        None,
        [],
        10,
    )[0]

    first = service.to_topic_signal(service.normalize_source(source))
    second = service.to_topic_signal(service.normalize_source(source))

    assert first == second
    assert first.confidence == 0.95
    assert source.reliability_tier is ReliabilityTier.TIER_1_OFFICIAL


def test_partial_failure_state_and_errors_survive_normalization() -> None:
    provider, service = _partial_search_result()
    search_result = provider.search_recent_issues_with_status(
        "국방 AI",
        None,
        None,
        [],
        20,
    )

    normalized = service.normalize_search_result(search_result)

    assert normalized.search_status is ExternalSearchStatus.PARTIAL
    assert normalized.errors == search_result.errors
    assert normalized.sources
    assert len(normalized.topic_signals) == len(normalized.sources)


def test_timeout_normalizes_to_empty_result_without_network_retry() -> None:
    provider, service = _partial_search_result()
    search_result = provider.search_recent_issues_with_status(
        "simulate-timeout",
        None,
        None,
        [],
        10,
    )

    normalized = service.normalize_search_result(search_result)

    assert normalized.search_status is ExternalSearchStatus.TIMEOUT
    assert normalized.sources == []
    assert normalized.topic_signals == []
    assert normalized.errors[0].retryable is True
