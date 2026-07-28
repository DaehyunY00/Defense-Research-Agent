"""Validated input and structured model output for topic generation."""

from typing import Annotated

from pydantic import Field, PositiveInt, StringConstraints

from defense_research_agent.domain.common import DomainModel, EntityId, Label
from defense_research_agent.domain.publication import PublicationType
from defense_research_agent.domain.search import PublicationSearchResult
from defense_research_agent.domain.topic import RecommendedOutputType, TopicSignal

type RequiredText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]


class TopicGeneratorInput(DomainModel):
    """Evidence and user constraints supplied to the topic generator."""

    normalized_signals: list[TopicSignal] = Field(default_factory=list)
    internal_search_results: list[PublicationSearchResult] = Field(default_factory=list)
    existing_publication_types: list[PublicationType] = Field(default_factory=list)
    user_interest_domains: list[Label] = Field(default_factory=list)
    excluded_domains: list[Label] = Field(default_factory=list)
    candidate_count: PositiveInt = Field(le=20)


class TopicCandidateDraft(DomainModel):
    """Strict structured output expected from a model before deterministic checks."""

    working_title: Label
    research_question: RequiredText
    trigger: RequiredText
    internal_context: RequiredText
    novelty_claim: RequiredText
    recommended_output: RecommendedOutputType
    supporting_signal_ids: list[EntityId] = Field(default_factory=list)
    related_publication_ids: list[EntityId] = Field(default_factory=list)
    known_limitations: list[Label] = Field(min_length=1)


class TopicCandidateBatch(DomainModel):
    """Batch schema passed directly to ``ModelGateway.generate_structured``."""

    candidates: list[TopicCandidateDraft] = Field(default_factory=list)
