"""Publication and publication-chunk domain models."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import (
    Field,
    HttpUrl,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

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


class PublicationPage(DomainModel):
    """One parser-produced page whose original text and locator are preserved."""

    page_number: PositiveInt
    text: str
    section_title: Label | None = None


class PublicationChunk(DomainModel):
    """A checksummed text chunk with publication and page provenance."""

    chunk_id: EntityId
    publication_id: EntityId
    text: str
    page_start: PositiveInt
    page_end: PositiveInt
    section_title: Label | None = None
    chunk_index: NonNegativeInt
    checksum: Checksum
    chunking_version: Label
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        """Reject blank chunks without altering original text or Korean characters."""
        if not value.strip():
            raise ValueError("chunk text must not be blank")
        return value

    @model_validator(mode="after")
    def page_range_must_be_ordered(self) -> "PublicationChunk":
        """Reject provenance ranges whose end precedes their start."""
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self
