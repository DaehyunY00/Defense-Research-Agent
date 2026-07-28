"""Topic-signal and research-topic candidate domain models."""

from datetime import date
from enum import StrEnum

from pydantic import Field, HttpUrl

from defense_research_agent.domain.common import (
    Confidence,
    DomainModel,
    EntityId,
    JsonObject,
    Label,
)


class RecommendedOutputType(StrEnum):
    """Allowed publication formats for a generated research topic."""

    DEFENSE_FORUM = "국방논단"
    KIDA_BRIEF = "KIDA Brief"
    DEFENSE_POLICY_RESEARCH = "국방정책연구"
    RESEARCH_REPORT = "연구보고서"

    @classmethod
    def _missing_(cls, value: object) -> "RecommendedOutputType | None":
        if not isinstance(value, str):
            return None
        aliases = {
            "defense_forum": cls.DEFENSE_FORUM,
            "kida_brief": cls.KIDA_BRIEF,
            "kida brief": cls.KIDA_BRIEF,
            "defense_policy_research": cls.DEFENSE_POLICY_RESEARCH,
            "정책연구": cls.DEFENSE_POLICY_RESEARCH,
            "research_report": cls.RESEARCH_REPORT,
        }
        return aliases.get(value.strip().casefold())


class TopicSignal(DomainModel):
    """An internal or external signal that may trigger a research topic."""

    signal_id: EntityId
    signal_type: Label
    title: Label
    summary: str | None = None
    event_date: date | None = None
    publication_ids: list[EntityId] = Field(default_factory=list)
    policy_domains: list[Label] = Field(default_factory=list)
    countries: list[Label] = Field(default_factory=list)
    organizations: list[Label] = Field(default_factory=list)
    keywords: list[Label] = Field(default_factory=list)
    confidence: Confidence
    source_ids: list[EntityId] = Field(default_factory=list)
    source_urls: list[HttpUrl] = Field(default_factory=list)
    raw_metadata: JsonObject = Field(default_factory=dict)


class TopicCandidate(DomainModel):
    """A proposed defense-policy research topic awaiting evaluation and approval."""

    candidate_id: EntityId
    working_title: Label
    research_question: str
    trigger: str | None = None
    internal_context: str | None = None
    novelty_claim: str | None = None
    recommended_output: RecommendedOutputType | None = None
    supporting_signal_ids: list[EntityId] = Field(default_factory=list)
    related_publication_ids: list[EntityId] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
