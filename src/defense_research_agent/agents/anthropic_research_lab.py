"""Assemble all seven research roles with Claude gateways and default routes."""

from collections.abc import Sequence
from dataclasses import dataclass

from defense_research_agent.agents.anthropic_model_gateway import (
    AnthropicClient,
    AnthropicModelGateway,
    AnthropicRuntimeSettings,
    build_anthropic_role_gateways,
)
from defense_research_agent.agents.research_lab import (
    MainResearcherAgent,
    StructuredResearchAgent,
    build_default_role_specs,
    required_worker_roles,
)
from defense_research_agent.domain import DataAnalysisDatasetDescriptor, ResearchRole


@dataclass(frozen=True, slots=True)
class AnthropicResearchLabAgents:
    """Fully routed agent set ready for injection into ResearchLabService."""

    main_researcher: MainResearcherAgent
    workers: dict[ResearchRole, StructuredResearchAgent]
    gateways: dict[ResearchRole, AnthropicModelGateway]


def build_anthropic_research_lab_agents(
    settings: AnthropicRuntimeSettings,
    *,
    data_analysis_catalog: Sequence[DataAnalysisDatasetDescriptor] = (),
    client: AnthropicClient | None = None,
) -> AnthropicResearchLabAgents:
    """Create one PI and six workers while sharing the provider connection pool."""
    gateways = build_anthropic_role_gateways(settings, client=client)
    specs = {spec.role: spec for spec in build_default_role_specs(settings.model_routes())}
    main_researcher = MainResearcherAgent(
        specs[ResearchRole.MAIN_RESEARCHER],
        gateways[ResearchRole.MAIN_RESEARCHER],
        data_analysis_catalog=data_analysis_catalog,
    )
    workers = {
        role: StructuredResearchAgent(specs[role], gateways[role])
        for role in required_worker_roles()
    }
    return AnthropicResearchLabAgents(
        main_researcher=main_researcher,
        workers=workers,
        gateways=gateways,
    )
