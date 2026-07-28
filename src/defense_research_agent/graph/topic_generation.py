"""LangGraph state and node for grounded topic candidate generation."""

from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from defense_research_agent.domain import (
    PublicationSearchResult,
    PublicationType,
    TopicCandidate,
    TopicGeneratorInput,
    TopicSignal,
)
from defense_research_agent.services.topic_generator import TopicGenerator


class TopicGenerationState(TypedDict):
    """State read and updated by the topic generation graph."""

    normalized_signals: list[TopicSignal]
    internal_search_results: list[PublicationSearchResult]
    existing_publication_types: list[PublicationType]
    user_interest_domains: list[str]
    excluded_domains: list[str]
    candidate_count: int
    topic_candidates: NotRequired[list[TopicCandidate]]


class TopicGenerationUpdate(TypedDict):
    """Partial state returned by the generation node."""

    topic_candidates: list[TopicCandidate]


def generate_topic_candidates(
    state: TopicGenerationState,
    topic_generator: TopicGenerator,
) -> TopicGenerationUpdate:
    """Generate candidates from graph state through the injected service."""
    generator_input = TopicGeneratorInput(
        normalized_signals=state["normalized_signals"],
        internal_search_results=state["internal_search_results"],
        existing_publication_types=state["existing_publication_types"],
        user_interest_domains=state["user_interest_domains"],
        excluded_domains=state["excluded_domains"],
        candidate_count=state["candidate_count"],
    )
    return {"topic_candidates": topic_generator.generate(generator_input)}


def build_topic_generation_graph(
    topic_generator: TopicGenerator,
) -> CompiledStateGraph[
    TopicGenerationState,
    None,
    TopicGenerationState,
    TopicGenerationState,
]:
    """Compile the one-node topic generation workflow."""

    def generation_node(state: TopicGenerationState) -> TopicGenerationUpdate:
        return generate_topic_candidates(state, topic_generator)

    builder = StateGraph(TopicGenerationState)
    builder.add_node("generate_topic_candidates", generation_node)
    builder.add_edge(START, "generate_topic_candidates")
    builder.add_edge("generate_topic_candidates", END)
    return builder.compile(name="topic-generation")
