"""End-to-end topic generation, evaluation, ranking, and human-review graph."""

from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from defense_research_agent.domain.evaluation import (
    AggregatedCandidateEvaluation,
    CandidateEvaluation,
)
from defense_research_agent.domain.publication import PublicationType
from defense_research_agent.domain.ranking import RankedTopic, RankingConfig
from defense_research_agent.domain.review import (
    ReviewSubmission,
    ReviewWorkflowStatus,
    TopicPlanningCard,
)
from defense_research_agent.domain.search import PublicationSearchResult
from defense_research_agent.domain.topic import TopicCandidate, TopicSignal
from defense_research_agent.domain.topic_generation import TopicGeneratorInput
from defense_research_agent.services.evaluation import (
    EvaluationRunner,
    aggregate_candidate_evaluations,
    write_evaluation_results,
)
from defense_research_agent.services.ranking import (
    diversify_candidates as diversify_ranked_candidates,
)
from defense_research_agent.services.ranking import (
    rank_candidates as calculate_ranked_candidates,
)
from defense_research_agent.services.ranking import write_ranked_candidates
from defense_research_agent.services.review import HumanReviewService
from defense_research_agent.services.topic_generator import TopicGenerator


class ResearchWorkflowState(TypedDict):
    """Serializable state for the complete offline pilot workflow."""

    run_id: str
    normalized_signals: list[TopicSignal]
    internal_search_results: list[PublicationSearchResult]
    existing_publication_types: list[PublicationType]
    user_interest_domains: list[str]
    excluded_domains: list[str]
    candidate_count: int
    topic_candidates: NotRequired[list[TopicCandidate]]
    candidate_evaluations: NotRequired[list[CandidateEvaluation]]
    aggregated_evaluations: NotRequired[list[AggregatedCandidateEvaluation]]
    ranked_candidates: NotRequired[list[RankedTopic]]
    review_submissions: NotRequired[list[ReviewSubmission]]
    review_status: NotRequired[ReviewWorkflowStatus]
    approved_candidate_ids: NotRequired[list[str]]
    pending_candidate_ids: NotRequired[list[str]]
    topic_planning_cards: NotRequired[list[TopicPlanningCard]]


class TopicCandidateUpdate(TypedDict):
    """Generation node update."""

    topic_candidates: list[TopicCandidate]


class CandidateEvaluationUpdate(TypedDict):
    """Parallel evaluator node update."""

    candidate_evaluations: list[CandidateEvaluation]


class AggregationUpdate(TypedDict):
    """Python aggregation node update."""

    aggregated_evaluations: list[AggregatedCandidateEvaluation]


class RankingUpdate(TypedDict):
    """Weighted ranking node update."""

    ranked_candidates: list[RankedTopic]


class ReviewUpdate(TypedDict):
    """Human-review gate node update."""

    review_status: ReviewWorkflowStatus
    approved_candidate_ids: list[str]
    pending_candidate_ids: list[str]
    review_submissions: list[ReviewSubmission]


class PlanningCardUpdate(TypedDict):
    """Approved planning-card node update."""

    topic_planning_cards: list[TopicPlanningCard]


def generate_topic_candidates(
    state: ResearchWorkflowState,
    topic_generator: TopicGenerator,
) -> TopicCandidateUpdate:
    """Generate candidates from grounded internal and external evidence."""
    generator_input = TopicGeneratorInput(
        normalized_signals=state["normalized_signals"],
        internal_search_results=state["internal_search_results"],
        existing_publication_types=state["existing_publication_types"],
        user_interest_domains=state["user_interest_domains"],
        excluded_domains=state["excluded_domains"],
        candidate_count=state["candidate_count"],
    )
    return {"topic_candidates": topic_generator.generate(generator_input)}


def parallel_evaluations(
    state: ResearchWorkflowState,
    evaluation_runner: EvaluationRunner,
) -> CandidateEvaluationUpdate:
    """Run all evaluator/candidate pairs concurrently with partial-failure isolation."""
    return {
        "candidate_evaluations": evaluation_runner.evaluate(
            state.get("topic_candidates", []),
            state["normalized_signals"],
        )
    }


def aggregate_evaluations(
    state: ResearchWorkflowState,
    artifacts_root: Path,
) -> AggregationUpdate:
    """Aggregate results in Python and persist an explainable run artifact."""
    evaluations = state.get("candidate_evaluations", [])
    aggregates = aggregate_candidate_evaluations(evaluations)
    write_evaluation_results(
        artifacts_root,
        state["run_id"],
        evaluations,
        aggregates,
    )
    return {"aggregated_evaluations": aggregates}


def rank_candidates(
    state: ResearchWorkflowState,
    config: RankingConfig,
) -> RankingUpdate:
    """Apply configurable weighted scoring and deterministic penalties."""
    return {
        "ranked_candidates": calculate_ranked_candidates(
            state.get("topic_candidates", []),
            state.get("aggregated_evaluations", []),
            state["normalized_signals"],
            config,
        )
    }


def diversify_candidates(
    state: ResearchWorkflowState,
    config: RankingConfig,
    artifacts_root: Path,
    selection_limit: int | None,
) -> RankingUpdate:
    """Apply optional portfolio diversity and write the ranked run output."""
    ranked = diversify_ranked_candidates(
        state.get("ranked_candidates", []),
        config,
        limit=selection_limit,
    )
    write_ranked_candidates(artifacts_root, state["run_id"], ranked, config)
    return {"ranked_candidates": ranked}


def human_review_interrupt(
    state: ResearchWorkflowState,
    review_service: HumanReviewService,
) -> ReviewUpdate:
    """Persist supplied decisions, then stop unless the human review gate is ready."""
    ranked_topics = state.get("ranked_candidates", [])
    for submission in state.get("review_submissions", []):
        review_service.record_decision(state["run_id"], ranked_topics, submission)
    gate = review_service.review_gate(state["run_id"], ranked_topics)
    return {
        "review_status": gate.status,
        "approved_candidate_ids": gate.approved_candidate_ids,
        "pending_candidate_ids": gate.pending_candidate_ids,
        "review_submissions": [],
    }


def generate_topic_planning_cards(
    state: ResearchWorkflowState,
    review_service: HumanReviewService,
    artifacts_root: Path,
) -> PlanningCardUpdate:
    """Generate final output only for explicitly approved candidates."""
    cards = review_service.generate_planning_cards(
        state["run_id"],
        state.get("ranked_candidates", []),
    )
    review_service.write_planning_cards(
        artifacts_root,
        state["run_id"],
        state.get("ranked_candidates", []),
    )
    return {"topic_planning_cards": cards}


def build_research_workflow_graph(
    topic_generator: TopicGenerator,
    evaluation_runner: EvaluationRunner,
    ranking_config: RankingConfig,
    review_service: HumanReviewService,
    artifacts_root: Path,
    *,
    selection_limit: int | None = None,
) -> CompiledStateGraph[
    ResearchWorkflowState,
    None,
    ResearchWorkflowState,
    ResearchWorkflowState,
]:
    """Compile the offline graph with artifact-backed human pause and resume."""

    def generation_node(state: ResearchWorkflowState) -> TopicCandidateUpdate:
        return generate_topic_candidates(state, topic_generator)

    def evaluation_node(state: ResearchWorkflowState) -> CandidateEvaluationUpdate:
        return parallel_evaluations(state, evaluation_runner)

    def aggregation_node(state: ResearchWorkflowState) -> AggregationUpdate:
        return aggregate_evaluations(state, artifacts_root)

    def ranking_node(state: ResearchWorkflowState) -> RankingUpdate:
        return rank_candidates(state, ranking_config)

    def diversity_node(state: ResearchWorkflowState) -> RankingUpdate:
        return diversify_candidates(
            state,
            ranking_config,
            artifacts_root,
            selection_limit,
        )

    def review_node(state: ResearchWorkflowState) -> ReviewUpdate:
        return human_review_interrupt(state, review_service)

    def planning_node(state: ResearchWorkflowState) -> PlanningCardUpdate:
        return generate_topic_planning_cards(state, review_service, artifacts_root)

    builder = StateGraph(ResearchWorkflowState)
    builder.add_node("generate_topic_candidates", generation_node)
    builder.add_node("parallel_evaluations", evaluation_node)
    builder.add_node("aggregate_evaluations", aggregation_node)
    builder.add_node("rank_candidates", ranking_node)
    builder.add_node("diversify_candidates", diversity_node)
    builder.add_node("human_review_interrupt", review_node)
    builder.add_node("generate_topic_planning_cards", planning_node)
    builder.add_conditional_edges(
        START,
        _resume_route,
        {
            "generate": "generate_topic_candidates",
            "review": "human_review_interrupt",
            "end": END,
        },
    )
    builder.add_edge("generate_topic_candidates", "parallel_evaluations")
    builder.add_edge("parallel_evaluations", "aggregate_evaluations")
    builder.add_edge("aggregate_evaluations", "rank_candidates")
    builder.add_edge("rank_candidates", "diversify_candidates")
    builder.add_edge("diversify_candidates", "human_review_interrupt")
    builder.add_conditional_edges(
        "human_review_interrupt",
        _review_route,
        {
            "cards": "generate_topic_planning_cards",
            "end": END,
        },
    )
    builder.add_edge("generate_topic_planning_cards", END)
    return builder.compile(name="research-topic-pilot")


def _resume_route(
    state: ResearchWorkflowState,
) -> Literal["generate", "review", "end"]:
    if state.get("topic_planning_cards"):
        return "end"
    if "ranked_candidates" in state:
        return "review"
    return "generate"


def _review_route(state: ResearchWorkflowState) -> Literal["cards", "end"]:
    return "cards" if state.get("review_status") is ReviewWorkflowStatus.READY_FOR_CARDS else "end"
