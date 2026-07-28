"""External issue provider interfaces, fixtures, and normalization."""

from defense_research_agent.issues.anthropic_web_search import (
    DEFAULT_OFFICIAL_SOURCE_DOMAINS,
    AnthropicOfficialSearchSettings,
    AnthropicOfficialSourceSearchProvider,
    AnthropicWebSearchClient,
)
from defense_research_agent.issues.base import (
    ExternalIssueProviderError,
    ExternalIssueProviderTimeout,
    ExternalIssueSearchProvider,
)
from defense_research_agent.issues.mock_provider import (
    MockExternalIssueSearchProvider,
)

__all__ = [
    "DEFAULT_OFFICIAL_SOURCE_DOMAINS",
    "AnthropicOfficialSearchSettings",
    "AnthropicOfficialSourceSearchProvider",
    "AnthropicWebSearchClient",
    "ExternalIssueProviderError",
    "ExternalIssueProviderTimeout",
    "ExternalIssueSearchProvider",
    "MockExternalIssueSearchProvider",
]
