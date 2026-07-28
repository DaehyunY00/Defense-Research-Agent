"""GCP adapters and dependency composition for the deployed research service."""

import os
from collections.abc import Mapping
from typing import Protocol, cast

from anthropic import Anthropic
from google.cloud import run_v2
from pydantic import Field, field_validator, model_validator

from defense_research_agent.agents import (
    AnthropicRuntimeSettings,
    build_anthropic_research_lab_agents,
)
from defense_research_agent.agents.anthropic_model_gateway import AnthropicClient
from defense_research_agent.domain import DomainModel, ResearchRole
from defense_research_agent.issues import (
    DEFAULT_OFFICIAL_SOURCE_DOMAINS,
    AnthropicOfficialSearchSettings,
    AnthropicOfficialSourceSearchProvider,
    AnthropicWebSearchClient,
)
from defense_research_agent.repositories import GcsResearchPublicationRepository
from defense_research_agent.repositories.research_projects import (
    FirestoreResearchProjectRepository,
)
from defense_research_agent.services.data_analysis_sandbox import (
    DataAnalysisSandboxAdapter,
    load_default_data_analysis_registry,
)
from defense_research_agent.services.research_lab import ResearchLabService
from defense_research_agent.services.research_projects import (
    ResearchJobDispatcher,
    ResearchProjectApplicationService,
    ResearchProjectRunner,
    ResearchRunStore,
    research_result_object_name,
)
from defense_research_agent.services.research_tools import (
    ExternalIssueSearchAdapter,
    InternalCorpusSearchAdapter,
    ResearchToolAdapter,
    ResearchToolRuntime,
)


class GcpResearchRuntimeSettings(DomainModel):
    """Non-secret application deployment settings supplied by Terraform."""

    project_id: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=100)
    research_job_name: str = Field(min_length=1, max_length=100)
    research_job_container: str = Field(default="research-worker", min_length=1, max_length=100)
    firestore_database: str = Field(default="(default)", min_length=1, max_length=100)
    project_collection: str = Field(
        default="research_projects",
        min_length=1,
        max_length=100,
    )
    artifact_bucket: str = Field(min_length=3, max_length=222)
    corpus_bucket: str | None = Field(default=None, min_length=3, max_length=222)
    corpus_manifest_object: str | None = Field(default=None, min_length=1, max_length=500)
    corpus_max_bytes: int = Field(default=100 * 1024 * 1024, gt=0, le=500 * 1024 * 1024)
    official_source_domains: tuple[str, ...] = DEFAULT_OFFICIAL_SOURCE_DOMAINS
    official_search_max_uses: int = Field(default=3, ge=1, le=10)

    @field_validator(
        "project_id",
        "region",
        "research_job_name",
        "research_job_container",
        "firestore_database",
        "project_collection",
        "artifact_bucket",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("GCP runtime setting must not be blank")
        return normalized

    @field_validator("corpus_bucket", "corpus_manifest_object")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("official_source_domains")
    @classmethod
    def validate_official_source_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return AnthropicOfficialSearchSettings(
            model_id="validation-only",
            allowed_domains=value,
        ).allowed_domains

    @model_validator(mode="after")
    def validate_corpus_configuration(self) -> "GcpResearchRuntimeSettings":
        if self.corpus_manifest_object is not None and self.corpus_bucket is None:
            raise ValueError("corpus_bucket is required when corpus_manifest_object is set")
        return self

    @property
    def corpus_enabled(self) -> bool:
        return self.corpus_bucket is not None and self.corpus_manifest_object is not None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "GcpResearchRuntimeSettings":
        """Load the exact settings emitted by the GCP deployment."""
        source = os.environ if environment is None else environment
        return cls(
            project_id=source.get("GOOGLE_CLOUD_PROJECT", ""),
            region=source.get("DEFENSE_RESEARCH_GCP_REGION", ""),
            research_job_name=source.get("DEFENSE_RESEARCH_JOB_NAME", ""),
            research_job_container=source.get(
                "DEFENSE_RESEARCH_JOB_CONTAINER",
                "research-worker",
            ),
            firestore_database=source.get(
                "DEFENSE_RESEARCH_FIRESTORE_DATABASE",
                "(default)",
            ),
            project_collection=source.get(
                "DEFENSE_RESEARCH_PROJECT_COLLECTION",
                "research_projects",
            ),
            artifact_bucket=source.get("DEFENSE_RESEARCH_ARTIFACT_BUCKET", ""),
            corpus_bucket=source.get("DEFENSE_RESEARCH_CORPUS_BUCKET") or None,
            corpus_manifest_object=(source.get("DEFENSE_RESEARCH_CORPUS_MANIFEST_OBJECT") or None),
            corpus_max_bytes=_parse_positive_int(
                source,
                "DEFENSE_RESEARCH_CORPUS_MAX_BYTES",
                100 * 1024 * 1024,
            ),
            official_source_domains=_parse_domains(
                source.get("DEFENSE_RESEARCH_OFFICIAL_SOURCE_DOMAINS", "")
            ),
            official_search_max_uses=_parse_positive_int(
                source,
                "DEFENSE_RESEARCH_OFFICIAL_SEARCH_MAX_USES",
                3,
            ),
        )


class _StorageBlob(Protocol):
    def upload_from_string(
        self,
        data: bytes,
        *,
        content_type: str,
        if_generation_match: int,
        timeout: int,
    ) -> object: ...

    def download_as_bytes(self, *, timeout: int) -> object: ...


class _StorageBucket(Protocol):
    def blob(self, blob_name: str) -> _StorageBlob: ...


class _StorageClient(Protocol):
    def bucket(self, bucket_name: str) -> _StorageBucket: ...


class _JobsClient(Protocol):
    def run_job(self, *, request: run_v2.RunJobRequest) -> object: ...


class GcsResearchRunStore(ResearchRunStore):
    """Create-only GCS store for complete schema-validated research runs."""

    def __init__(
        self,
        bucket_name: str,
        *,
        client: _StorageClient | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if not bucket_name.strip() or timeout_seconds <= 0:
            raise ValueError("artifact bucket and positive timeout are required")
        if client is None:
            from google.cloud import storage  # type: ignore[attr-defined]

            client = cast(_StorageClient, storage.Client())
        self._bucket = client.bucket(bucket_name.strip())
        self._timeout_seconds = timeout_seconds

    def put(self, project_id: str, payload: bytes) -> str:
        object_name = research_result_object_name(project_id)
        self._bucket.blob(object_name).upload_from_string(
            payload,
            content_type="application/json",
            if_generation_match=0,
            timeout=self._timeout_seconds,
        )
        return object_name

    def get(self, object_name: str) -> bytes:
        payload = self._bucket.blob(_validate_object_name(object_name)).download_as_bytes(
            timeout=self._timeout_seconds
        )
        if not isinstance(payload, bytes):
            raise TypeError("Cloud Storage returned non-bytes research output")
        return payload


class GcpCloudRunResearchJobDispatcher(ResearchJobDispatcher):
    """Start a Cloud Run Job with only a validated project ID override."""

    def __init__(
        self,
        settings: GcpResearchRuntimeSettings,
        *,
        client: _JobsClient | None = None,
    ) -> None:
        self._job_resource = (
            f"projects/{settings.project_id}/locations/{settings.region}/"
            f"jobs/{settings.research_job_name}"
        )
        self._container_name = settings.research_job_container
        if client is None:
            client = cast(_JobsClient, run_v2.JobsClient())
        self._client = client

    def dispatch(self, project_id: str) -> str:
        research_result_object_name(project_id)
        override = run_v2.RunJobRequest.Overrides.ContainerOverride(
            name=self._container_name,
            env=[
                run_v2.EnvVar(
                    name="DEFENSE_RESEARCH_PROJECT_ID",
                    value=project_id,
                )
            ],
        )
        request = run_v2.RunJobRequest(
            name=self._job_resource,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[override],
                task_count=1,
            ),
        )
        operation = self._client.run_job(request=request)
        raw_operation = getattr(operation, "operation", None)
        operation_name = getattr(raw_operation, "name", None)
        if isinstance(operation_name, str) and operation_name:
            return operation_name
        return f"{self._job_resource}:submitted"


def build_gcp_research_application(
    settings: GcpResearchRuntimeSettings | None = None,
) -> ResearchProjectApplicationService:
    """Create API dependencies from application default credentials."""
    resolved = settings or GcpResearchRuntimeSettings.from_environment()
    repository = _repository(resolved)
    run_store = GcsResearchRunStore(resolved.artifact_bucket)
    dispatcher = GcpCloudRunResearchJobDispatcher(resolved)
    return ResearchProjectApplicationService(repository, dispatcher, run_store)


def build_gcp_research_runner(
    settings: GcpResearchRuntimeSettings | None = None,
    anthropic_settings: AnthropicRuntimeSettings | None = None,
) -> ResearchProjectRunner:
    """Create a worker that exposes the Claude secret only inside the job."""
    resolved = settings or GcpResearchRuntimeSettings.from_environment()
    claude = anthropic_settings or AnthropicRuntimeSettings.from_environment()
    repository = _repository(resolved)
    run_store = GcsResearchRunStore(resolved.artifact_bucket)

    def lab_factory() -> ResearchLabService:
        data_registry = load_default_data_analysis_registry()
        shared_client = Anthropic(
            api_key=claude.api_key.get_secret_value(),
            timeout=claude.timeout_seconds,
            max_retries=claude.max_retries,
        )
        routed_agents = build_anthropic_research_lab_agents(
            claude,
            data_analysis_catalog=data_registry.catalog(),
            client=cast(AnthropicClient, shared_client),
        )
        adapters: list[ResearchToolAdapter] = [
            DataAnalysisSandboxAdapter(data_registry),
            ExternalIssueSearchAdapter(
                AnthropicOfficialSourceSearchProvider(
                    cast(AnthropicWebSearchClient, shared_client),
                    AnthropicOfficialSearchSettings(
                        model_id=claude.role_model_ids[ResearchRole.CURRENT_ISSUE_RESEARCHER],
                        allowed_domains=resolved.official_source_domains,
                        max_uses=resolved.official_search_max_uses,
                    ),
                )
            ),
        ]
        if resolved.corpus_enabled:
            if resolved.corpus_bucket is None or resolved.corpus_manifest_object is None:
                raise ValueError("enabled corpus configuration is incomplete")
            adapters.append(
                InternalCorpusSearchAdapter(
                    GcsResearchPublicationRepository(
                        resolved.corpus_bucket,
                        resolved.corpus_manifest_object,
                        max_index_bytes=resolved.corpus_max_bytes,
                    )
                )
            )
        tool_runtime = ResearchToolRuntime(adapters)
        return ResearchLabService(
            routed_agents.main_researcher,
            routed_agents.workers,
            tool_runtime=tool_runtime,
        )

    return ResearchProjectRunner(repository, run_store, lab_factory)


def _repository(
    settings: GcpResearchRuntimeSettings,
) -> FirestoreResearchProjectRepository:
    return FirestoreResearchProjectRepository(
        project_id=settings.project_id,
        database=settings.firestore_database,
        collection=settings.project_collection,
    )


def _validate_object_name(object_name: str) -> str:
    normalized = object_name.strip()
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("research result object name is invalid")
    return normalized


def _parse_positive_int(
    environment: Mapping[str, str],
    key: str,
    default: int,
) -> int:
    raw_value = environment.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _parse_domains(raw_value: str) -> tuple[str, ...]:
    domains = tuple(part.strip() for part in raw_value.split(",") if part.strip())
    return domains or DEFAULT_OFFICIAL_SOURCE_DOMAINS
