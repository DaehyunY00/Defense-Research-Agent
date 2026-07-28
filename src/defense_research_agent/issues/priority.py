"""Deterministic external-source priority rules."""

from defense_research_agent.domain import (
    ExternalSource,
    ExternalSourceType,
    ReliabilityTier,
)

_RELIABILITY_PRIORITY = {
    ReliabilityTier.TIER_1_OFFICIAL: 0,
    ReliabilityTier.TIER_2_INSTITUTIONAL: 1,
    ReliabilityTier.TIER_3_NEWS: 2,
    ReliabilityTier.TIER_4_UNVERIFIED: 3,
}
_SOURCE_TYPE_PRIORITY = {
    ExternalSourceType.GOVERNMENT_POLICY: 0,
    ExternalSourceType.DEFENSE_PRESS_RELEASE: 1,
    ExternalSourceType.LEGISLATIVE_OVERSIGHT: 2,
    ExternalSourceType.THINK_TANK_REPORT: 3,
    ExternalSourceType.NEWS_ARTICLE: 4,
    ExternalSourceType.OTHER: 5,
}


def external_source_priority(source: ExternalSource) -> tuple[int, int, int, str]:
    """Place official primary material first, then newer and stable IDs."""
    publication_ordinal = (
        source.publication_date.toordinal() if source.publication_date is not None else -1
    )
    return (
        _RELIABILITY_PRIORITY[source.reliability_tier],
        _SOURCE_TYPE_PRIORITY[source.source_type],
        -publication_ordinal,
        source.source_id,
    )
