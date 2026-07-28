"""Publication and publication-chunk domain models."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, HttpUrl, NonNegativeInt, PositiveInt, field_validator

from defense_research_agent.domain.common import (
    Checksum,
    DomainModel,
    EntityId,
    JsonObject,
    Label,
    LanguageCode,
)


class PublicationType(StrEnum):
    """Canonical publication categories used by the application."""

    DEFENSE_FORUM = "defense_forum"
    KIDA_BRIEF = "kida_brief"
    DEFENSE_POLICY_RESEARCH = "defense_policy_research"
    RESEARCH_REPORT = "research_report"
    SECURITY_STRATEGY_FOCUS = "security_strategy_focus"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object) -> "PublicationType | None":
        if not isinstance(value, str):
            return None

        aliases = {
            "국방논단": cls.DEFENSE_FORUM,
            "brief": cls.KIDA_BRIEF,
            "kida brief": cls.KIDA_BRIEF,
            "국방정책연구": cls.DEFENSE_POLICY_RESEARCH,
            "연구보고서": cls.RESEARCH_REPORT,
            "안보전략포커스": cls.SECURITY_STRATEGY_FOCUS,
        }
        return aliases.get(value.strip().casefold())


class ResearchPublication(DomainModel):
    """A normalized research publication with optional enriched bibliography."""

    publication_id: EntityId
    publication_type: PublicationType
    title: Label | None = None
    subtitle: Label | None = None
    authors: list[Label] = Field(default_factory=list)
    organization: Label | None = None
    publication_date: date | None = None
    issue_number: Label | None = None
    volume: Label | None = None
    abstract: str | None = None
    keywords: list[Label] = Field(default_factory=list)
    language: LanguageCode | None = None
    source_url: HttpUrl | None = None
    local_path: str | None = None
    raw_metadata: JsonObject = Field(default_factory=dict)
    content: str | None = None
    created_at: datetime | None = None
    checksum: Checksum | None = None


class PublicationChunk(DomainModel):
    """A page-aware, ordered chunk derived from a publication."""

    chunk_id: EntityId
    publication_id: EntityId
    section: Label | None = None
    page: PositiveInt | None = None
    sequence: NonNegativeInt
    text: str
    token_count: NonNegativeInt | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        """Reject blank chunks without altering original text or Korean characters."""
        if not value.strip():
            raise ValueError("chunk text must not be blank")
        return value
