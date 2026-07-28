"""Contract tests for GCP job dispatch and immutable research artifacts."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.cloud import run_v2

from defense_research_agent.services.gcp_research_runtime import (
    GcpCloudRunResearchJobDispatcher,
    GcpResearchRuntimeSettings,
    GcsResearchRunStore,
)


class FakeBlob:
    def __init__(self) -> None:
        self.payload: bytes | None = None
        self.uploads: list[tuple[str, int, int]] = []

    def upload_from_string(
        self,
        data: bytes,
        *,
        content_type: str,
        if_generation_match: int,
        timeout: int,
    ) -> None:
        self.payload = data
        self.uploads.append((content_type, if_generation_match, timeout))

    def download_as_bytes(self, *, timeout: int) -> bytes:
        assert timeout == 60
        if self.payload is None:
            raise RuntimeError("missing")
        return self.payload


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, blob_name: str) -> FakeBlob:
        return self.blobs.setdefault(blob_name, FakeBlob())


class FakeStorageClient:
    def __init__(self) -> None:
        self.bucket_value = FakeBucket()
        self.bucket_names: list[str] = []

    def bucket(self, bucket_name: str) -> FakeBucket:
        self.bucket_names.append(bucket_name)
        return self.bucket_value


class FakeJobsClient:
    def __init__(self) -> None:
        self.requests: list[run_v2.RunJobRequest] = []

    def run_job(self, *, request: run_v2.RunJobRequest) -> object:
        self.requests.append(request)
        return SimpleNamespace(operation=SimpleNamespace(name="operations/research-1"))


def _settings() -> GcpResearchRuntimeSettings:
    return GcpResearchRuntimeSettings(
        project_id="test-project",
        region="asia-northeast3",
        research_job_name="defense-research-worker",
        artifact_bucket="test-project-research-artifacts",
    )


def test_settings_require_only_non_secret_deployment_values() -> None:
    settings = GcpResearchRuntimeSettings.from_environment(
        {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "DEFENSE_RESEARCH_GCP_REGION": "asia-northeast3",
            "DEFENSE_RESEARCH_JOB_NAME": "worker",
            "DEFENSE_RESEARCH_ARTIFACT_BUCKET": "artifact-bucket",
            "DEFENSE_RESEARCH_CORPUS_BUCKET": "corpus-bucket",
            "DEFENSE_RESEARCH_CORPUS_MANIFEST_OBJECT": ("corpus/manifests/approved.json"),
            "DEFENSE_RESEARCH_CORPUS_MAX_BYTES": "4096",
            "DEFENSE_RESEARCH_OFFICIAL_SOURCE_DOMAINS": "mnd.go.kr,defense.gov",
            "DEFENSE_RESEARCH_OFFICIAL_SEARCH_MAX_USES": "4",
        }
    )

    assert settings.project_id == "test-project"
    assert settings.corpus_enabled is True
    assert settings.corpus_max_bytes == 4096
    assert settings.official_source_domains == ("mnd.go.kr", "defense.gov")
    assert settings.official_search_max_uses == 4
    assert "api_key" not in GcpResearchRuntimeSettings.model_fields


def test_dispatcher_passes_only_project_id_as_job_override() -> None:
    fake_client = FakeJobsClient()
    dispatcher = GcpCloudRunResearchJobDispatcher(
        _settings(),
        client=cast(Any, fake_client),
    )

    operation_name = dispatcher.dispatch("research-1")

    assert operation_name == "operations/research-1"
    request = fake_client.requests[0]
    assert request.name.endswith("/jobs/defense-research-worker")
    overrides = request.overrides
    assert overrides.task_count == 1
    container = overrides.container_overrides[0]
    assert container.name == "research-worker"
    assert [(item.name, item.value) for item in container.env] == [
        ("DEFENSE_RESEARCH_PROJECT_ID", "research-1")
    ]

    with pytest.raises(ValueError, match="safe result object"):
        dispatcher.dispatch("../escape")


def test_gcs_store_uses_create_only_upload_and_exact_download() -> None:
    client = FakeStorageClient()
    store = GcsResearchRunStore(
        "artifact-bucket",
        client=cast(Any, client),
    )

    object_name = store.put("research-1", b'{"result":"ok"}')

    assert object_name == "research-projects/research-1/research_lab_run.json"
    blob = client.bucket_value.blobs[object_name]
    assert blob.uploads == [("application/json", 0, 60)]
    assert store.get(object_name) == b'{"result":"ok"}'

    with pytest.raises(ValueError, match="invalid"):
        store.get("../escape")
