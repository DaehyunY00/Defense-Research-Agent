"""Allow-listed research tool adapters for internal and external evidence."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import cast

from pydantic import JsonValue

from defense_research_agent.domain import (
    ExternalSearchStatus,
    JsonObject,
    ResearchBrief,
    ResearchTask,
    ResearchToolContext,
    ResearchToolEvidence,
    ResearchToolFailure,
    ResearchToolOutput,
    ToolCapability,
)
from defense_research_agent.issues import ExternalIssueSearchProvider
from defense_research_agent.repositories import ResearchPublicationRepository
from defense_research_agent.services.external_issues import ExternalIssueNormalizationService

_MAX_EXCERPT_LENGTH = 4_000


class ResearchToolAdapter(ABC):
    """Execute one explicit capability without model-controlled arbitrary calls."""

    capability: ToolCapability

    @abstractmethod
    def execute(self, brief: ResearchBrief, task: ResearchTask) -> ResearchToolOutput:
        """Return traceable evidence and sanitized failures for one task."""


class InternalCorpusSearchAdapter(ResearchToolAdapter):
    """Adapt the existing publication repository to research-lab evidence."""

    capability = ToolCapability.INTERNAL_CORPUS_SEARCH

    def __init__(
        self,
        repository: ResearchPublicationRepository,
        *,
        limit_per_query: int = 5,
    ) -> None:
        if limit_per_query <= 0:
            raise ValueError("limit_per_query must be positive")
        self._repository = repository
        self._limit_per_query = limit_per_query

    def execute(self, brief: ResearchBrief, task: ResearchTask) -> ResearchToolOutput:
        """Search each explicit query and de-duplicate publications by stable ID."""
        evidence_by_id: dict[str, ResearchToolEvidence] = {}
        for query in _effective_queries(brief, task):
            results = self._repository.search(
                query,
                limit=self._limit_per_query,
            )
            for result in results:
                publication = result.publication
                evidence_by_id.setdefault(
                    publication.publication_id,
                    ResearchToolEvidence(
                        evidence_id=publication.publication_id,
                        capability=self.capability,
                        title=publication.title or publication.publication_id,
                        excerpt=_truncate(publication.abstract or publication.content),
                        source_type=f"publication:{publication.publication_type.value}",
                        locator=publication.local_path,
                        source_url=(
                            str(publication.source_url)
                            if publication.source_url is not None
                            else None
                        ),
                        metadata={
                            "query": query,
                            "score": result.score,
                            "matched_fields": cast(
                                JsonValue,
                                [field.value for field in result.matched_fields],
                            ),
                            "matched_terms": cast(JsonValue, result.matched_terms),
                            "publication_type": publication.publication_type.value,
                        },
                    ),
                )
        return ResearchToolOutput(
            capability=self.capability,
            evidence=list(evidence_by_id.values()),
        )


class ExternalIssueSearchAdapter(ResearchToolAdapter):
    """Adapt the current issue provider and normalizer to untrusted evidence."""

    capability = ToolCapability.EXTERNAL_SOURCE_SEARCH

    def __init__(
        self,
        provider: ExternalIssueSearchProvider,
        *,
        limit_per_query: int = 5,
        normalization_service: ExternalIssueNormalizationService | None = None,
    ) -> None:
        if limit_per_query <= 0:
            raise ValueError("limit_per_query must be positive")
        self._provider = provider
        self._limit_per_query = limit_per_query
        self._normalization_service = normalization_service or ExternalIssueNormalizationService()

    def execute(self, brief: ResearchBrief, task: ResearchTask) -> ResearchToolOutput:
        """Search, normalize, and preserve provider partial failures."""
        evidence_by_id: dict[str, ResearchToolEvidence] = {}
        failures: list[ResearchToolFailure] = []
        for query in _effective_queries(brief, task):
            search_result = self._provider.search_recent_issues_with_status(
                query=query,
                start_date=brief.evidence_start_date,
                end_date=brief.evidence_end_date,
                domains=task.policy_domains,
                limit=self._limit_per_query,
            )
            normalized = self._normalization_service.normalize_search_result(
                search_result,
                limit=self._limit_per_query,
            )
            for error in normalized.errors:
                failures.append(
                    ResearchToolFailure(
                        capability=self.capability,
                        code=error.code,
                        message=error.message,
                        retryable=error.retryable,
                    )
                )
            for source in normalized.sources:
                evidence_by_id.setdefault(
                    source.source_id,
                    ResearchToolEvidence(
                        evidence_id=source.source_id,
                        capability=self.capability,
                        title=source.title,
                        excerpt=_truncate(source.snippet),
                        source_type=f"external:{source.source_type.value}",
                        locator=(
                            source.publication_date.isoformat()
                            if source.publication_date is not None
                            else None
                        ),
                        source_url=str(source.url),
                        untrusted_external_content=True,
                        metadata=_external_metadata(
                            query,
                            source.publisher,
                            source.reliability_tier.value,
                            source.reviewed,
                            normalized.search_status,
                            (
                                source.collected_at.isoformat()
                                if source.collected_at is not None
                                else None
                            ),
                            source.provider_metadata,
                        ),
                    ),
                )
        return ResearchToolOutput(
            capability=self.capability,
            evidence=list(evidence_by_id.values()),
            failures=failures,
        )


class ResearchToolRuntime:
    """Resolve requested capabilities to configured adapters and merge outputs."""

    def __init__(self, adapters: Sequence[ResearchToolAdapter]) -> None:
        by_capability: dict[ToolCapability, ResearchToolAdapter] = {}
        for adapter in adapters:
            if adapter.capability in by_capability:
                raise ValueError(f"duplicate research tool adapter: {adapter.capability.value}")
            by_capability[adapter.capability] = adapter
        self._adapters = by_capability

    def collect(self, brief: ResearchBrief, task: ResearchTask) -> ResearchToolContext:
        """Execute only explicitly requested capabilities and retain all failures."""
        requested_tools = list(dict.fromkeys(task.requested_tools))
        evidence_by_id: dict[str, ResearchToolEvidence] = {}
        failures: list[ResearchToolFailure] = []
        for capability in requested_tools:
            adapter = self._adapters.get(capability)
            if adapter is None:
                failures.append(
                    ResearchToolFailure(
                        capability=capability,
                        code="adapter_unavailable",
                        message=f"no adapter is configured for {capability.value}",
                    )
                )
                continue
            try:
                output = adapter.execute(brief, task)
                if output.capability is not capability:
                    raise ValueError("tool adapter returned a different capability")
                for evidence in output.evidence:
                    if evidence.capability is not capability:
                        raise ValueError("tool evidence returned a different capability")
                    evidence_by_id.setdefault(evidence.evidence_id, evidence)
                failures.extend(output.failures)
            except Exception as error:
                failures.append(
                    ResearchToolFailure(
                        capability=capability,
                        code="adapter_failure",
                        message=f"{type(error).__name__}: adapter execution failed",
                    )
                )
        return ResearchToolContext(
            project_id=brief.project_id,
            task_id=task.task_id,
            role=task.role,
            requested_tools=requested_tools,
            evidence=list(evidence_by_id.values()),
            failures=failures,
        )


def empty_tool_context(brief: ResearchBrief, task: ResearchTask) -> ResearchToolContext:
    """Build an explicit empty context for tasks that request no tools."""
    return ResearchToolContext(
        project_id=brief.project_id,
        task_id=task.task_id,
        role=task.role,
        requested_tools=list(task.requested_tools),
    )


def unavailable_tool_context(
    brief: ResearchBrief,
    task: ResearchTask,
) -> ResearchToolContext:
    """Record requested tools when no runtime has been configured."""
    return ResearchToolContext(
        project_id=brief.project_id,
        task_id=task.task_id,
        role=task.role,
        requested_tools=list(task.requested_tools),
        failures=[
            ResearchToolFailure(
                capability=capability,
                code="tool_runtime_unavailable",
                message="research tool runtime is not configured",
            )
            for capability in dict.fromkeys(task.requested_tools)
        ],
    )


def _effective_queries(brief: ResearchBrief, task: ResearchTask) -> tuple[str, ...]:
    return tuple(task.search_queries) or (brief.question,)


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_EXCERPT_LENGTH]


def _external_metadata(
    query: str,
    publisher: str,
    reliability_tier: str,
    reviewed: bool,
    search_status: ExternalSearchStatus,
    collected_at: str | None,
    provider_metadata: JsonObject,
) -> JsonObject:
    return {
        **provider_metadata,
        "query": query,
        "publisher": publisher,
        "reliability_tier": reliability_tier,
        "reviewed": reviewed,
        "search_status": search_status.value,
        "collected_at": collected_at,
    }
