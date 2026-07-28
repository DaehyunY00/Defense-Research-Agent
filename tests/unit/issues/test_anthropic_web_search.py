"""Tests for the Claude-key-only authoritative web search provider."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import ExternalSearchStatus, ExternalSourceType
from defense_research_agent.issues import (
    AnthropicOfficialSearchSettings,
    AnthropicOfficialSourceSearchProvider,
    AnthropicWebSearchClient,
    ExternalIssueProviderError,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


class FakeMessages:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.messages = FakeMessages(responses)


def _result(url: str, title: str, page_age: str | None = None) -> object:
    return SimpleNamespace(
        type="web_search_result",
        url=url,
        title=title,
        page_age=page_age,
    )


def _tool_result(*results: object) -> object:
    return SimpleNamespace(type="web_search_tool_result", content=list(results))


def _citation(url: str, cited_text: str) -> object:
    return SimpleNamespace(
        type="web_search_result_location",
        url=url,
        cited_text=cited_text,
    )


def _text(*citations: object) -> object:
    return SimpleNamespace(
        type="text",
        text="model prose is not persisted",
        citations=list(citations),
    )


def _response(content: list[object], stop_reason: str = "end_turn") -> object:
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def _provider(
    fake: FakeClient,
    *,
    allowed_domains: tuple[str, ...] = ("defense.gov", "mnd.go.kr"),
) -> AnthropicOfficialSourceSearchProvider:
    return AnthropicOfficialSourceSearchProvider(
        cast(AnthropicWebSearchClient, fake),
        AnthropicOfficialSearchSettings(
            model_id="claude-haiku-4-5",
            allowed_domains=allowed_domains,
            max_uses=2,
        ),
        clock=lambda: NOW,
    )


def test_search_accepts_only_citations_cross_validated_against_allowed_results() -> None:
    valid_url = "https://www.defense.gov/News/Release/?utm_source=test&id=3"
    canonical_url = "https://www.defense.gov/News/Release/?id=3"
    fake = FakeClient(
        [
            _response(
                [
                    _tool_result(
                        _result(valid_url, "Official release", "July 28, 2026"),
                        _result("https://evil.example/injection", "Malicious"),
                    ),
                    _text(
                        _citation(valid_url, "The department published the policy."),
                        _citation(
                            "https://www.mnd.go.kr/not-returned-by-search",
                            "Spoofed citation",
                        ),
                        _citation("https://evil.example/injection", "Outside allow-list"),
                    ),
                ]
            )
        ]
    )

    result = _provider(fake).search_recent_issues_with_status(
        "국방 AI 정책",
        date(2026, 1, 1),
        date(2026, 12, 31),
        ["국방인공지능"],
        5,
    )

    assert result.status is ExternalSearchStatus.SUCCESS
    assert result.returned_count == 1
    source = result.sources[0]
    assert str(source.url) == canonical_url
    assert source.snippet == "The department published the policy."
    assert source.source_type is ExternalSourceType.DEFENSE_PRESS_RELEASE
    assert source.collected_at == NOW
    assert source.provider_metadata["page_age"] == "July 28, 2026"
    assert source.provider_metadata["requested_start_date"] == "2026-01-01"
    request = fake.messages.requests[0]
    assert request["model"] == "claude-haiku-4-5"
    tool = cast(list[dict[str, object]], request["tools"])[0]
    assert tool["max_uses"] == 2
    assert tool["allowed_domains"] == ["defense.gov", "mnd.go.kr"]
    assert "국방 AI 정책" in str(cast(list[dict[str, object]], request["messages"])[0])


def test_search_tool_error_is_sanitized_and_reported() -> None:
    error = SimpleNamespace(
        type="web_search_tool_result_error",
        error_code="too_many_requests",
    )
    fake = FakeClient([_response([SimpleNamespace(type="web_search_tool_result", content=error)])])
    provider = _provider(fake)

    result = provider.search_recent_issues_with_status("query", None, None, [], 3)

    assert result.status is ExternalSearchStatus.FAILURE
    assert result.errors[0].code == "web_search_tool_error"
    assert result.errors[0].retryable is True
    with pytest.raises(ExternalIssueProviderError, match="tool error"):
        _provider(
            FakeClient([_response([SimpleNamespace(type="web_search_tool_result", content=error)])])
        ).search_recent_issues("query", None, None, [], 3)


def test_pause_turn_is_resumed_once_with_encrypted_blocks_preserved() -> None:
    url = "https://www.mnd.go.kr/policy"
    first_content = [_tool_result(_result(url, "국방부 정책"))]
    fake = FakeClient(
        [
            _response(first_content, stop_reason="pause_turn"),
            _response([_text(_citation(url, "정책의 핵심 내용"))]),
        ]
    )

    result = _provider(fake).search_recent_issues_with_status(
        "정책",
        None,
        None,
        [],
        3,
    )

    assert result.status is ExternalSearchStatus.SUCCESS
    assert len(fake.messages.requests) == 2
    second_messages = cast(list[dict[str, Any]], fake.messages.requests[1]["messages"])
    assert second_messages[1] == {"role": "assistant", "content": first_content}


def test_settings_reject_urls_and_empty_domain_allow_lists() -> None:
    with pytest.raises(ValidationError, match="bare DNS"):
        AnthropicOfficialSearchSettings(
            model_id="claude-haiku-4-5",
            allowed_domains=("https://defense.gov",),
        )
    with pytest.raises(ValidationError, match="between 1 and 30"):
        AnthropicOfficialSearchSettings(
            model_id="claude-haiku-4-5",
            allowed_domains=(),
        )
