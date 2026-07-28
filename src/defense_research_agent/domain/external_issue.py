"""Validated models for untrusted external defense and security issue sources."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, HttpUrl, NonNegativeInt, StringConstraints, field_validator

from defense_research_agent.domain.common import (
    DomainModel,
    EntityId,
    JsonObject,
    Label,
    LanguageCode,
)
from defense_research_agent.domain.topic import TopicSignal

type ErrorCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9_]+$",
    ),
]


class ExternalSourceType(StrEnum):
    """Observed classes of external policy evidence."""

    GOVERNMENT_POLICY = "government_policy"
    DEFENSE_PRESS_RELEASE = "defense_press_release"
    LEGISLATIVE_OVERSIGHT = "legislative_oversight"
    THINK_TANK_REPORT = "think_tank_report"
    NEWS_ARTICLE = "news_article"
    OTHER = "other"


class ReliabilityTier(StrEnum):
    """Coarse provenance tier used only for deterministic source priority."""

    TIER_1_OFFICIAL = "tier_1_official"
    TIER_2_INSTITUTIONAL = "tier_2_institutional"
    TIER_3_NEWS = "tier_3_news"
    TIER_4_UNVERIFIED = "tier_4_unverified"


class ExternalContentTrust(StrEnum):
    """Trust marking applied before any human source review."""

    UNTRUSTED = "untrusted"


class SourceRelationType(StrEnum):
    """Directed relationship between a source and another source record."""

    REPORTS_ON = "reports_on"
    SUMMARIZES = "summarizes"
    DUPLICATE_OF = "duplicate_of"
    HAS_DUPLICATE = "has_duplicate"


class ExternalSourceRelationship(DomainModel):
    """A typed edge that can connect news coverage to an original source."""

    relation_type: SourceRelationType
    target_source_id: EntityId


class ExternalSource(DomainModel):
    """One externally supplied record, always treated as untrusted content."""

    source_id: EntityId
    title: Label
    publisher: Label
    publication_date: date | None = None
    url: HttpUrl
    source_type: ExternalSourceType
    snippet: str | None = None
    language: LanguageCode | None = None
    policy_domains: list[Label] = Field(default_factory=list)
    countries: list[Label] = Field(default_factory=list)
    reliability_tier: ReliabilityTier
    relationships: list[ExternalSourceRelationship] = Field(default_factory=list)
    content_trust: ExternalContentTrust = ExternalContentTrust.UNTRUSTED
    reviewed: bool = False
    collected_at: datetime | None = None
    provider_metadata: JsonObject = Field(default_factory=dict)

    @field_validator("publication_date", mode="before")
    @classmethod
    def normalize_publication_date(cls, value: object) -> object:
        """Accept common machine-readable date variants without inventing a date."""
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            return None
        cleaned = cleaned.replace(".", "-").replace("/", "-")
        if "T" in cleaned:
            try:
                return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
            except ValueError:
                return cleaned
        return cleaned

    @field_validator("collected_at")
    @classmethod
    def validate_collected_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("collected_at must be timezone-aware")
        return value


class ExternalSearchStatus(StrEnum):
    """Outcome state for a provider call."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    TIMEOUT = "timeout"


class ExternalSearchError(DomainModel):
    """Sanitized provider or item-level failure information."""

    code: ErrorCode
    message: str
    retryable: bool = False
    source_reference: str | None = None


class ExternalIssueSearchResult(DomainModel):
    """Status-bearing companion to the list-returning provider contract."""

    status: ExternalSearchStatus
    sources: list[ExternalSource] = Field(default_factory=list)
    errors: list[ExternalSearchError] = Field(default_factory=list)
    requested_limit: NonNegativeInt
    returned_count: NonNegativeInt


class ExternalIssueNormalizationResult(DomainModel):
    """Normalized, de-duplicated sources and their derived topic signals."""

    search_status: ExternalSearchStatus
    sources: list[ExternalSource] = Field(default_factory=list)
    topic_signals: list[TopicSignal] = Field(default_factory=list)
    errors: list[ExternalSearchError] = Field(default_factory=list)
    duplicates_removed: NonNegativeInt
