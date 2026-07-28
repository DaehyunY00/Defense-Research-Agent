"""Contract tests for the GCS and Cloud Run sandbox validation backend."""

from pathlib import Path
from typing import cast

import pytest
from google.cloud import run_v2

from defense_research_agent.domain import (
    CodeSandboxCheck,
    CodeSandboxCheckResult,
    CodeSandboxValidation,
    SandboxJobRequest,
    SandboxJobResultEnvelope,
    SandboxJobStatus,
)
from defense_research_agent.services.code_sandbox import (
    SandboxValidationUnavailableError,
)
from defense_research_agent.services.gcp_code_sandbox import (
    GcpCloudRunJobValidationRunner,
    GcpCloudRunSandboxJobExecutor,
    SandboxJobExecutor,
    SandboxObjectStore,
    create_workspace_bundle,
)


class MemoryObjectStore(SandboxObjectStore):
    """Create-only in-memory object store used by remote-runner tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_bytes(self, object_name: str, payload: bytes, content_type: str) -> None:
        del content_type
        if object_name in self.objects:
            raise RuntimeError("object already exists")
        self.objects[object_name] = payload

    def download_bytes(self, object_name: str) -> bytes:
        return self.objects[object_name]


class CompletingJobExecutor(SandboxJobExecutor):
    """Fake Cloud Run executor that writes a bound structured result."""

    def __init__(self, store: MemoryObjectStore, *, corrupt_checksum: bool = False) -> None:
        self._store = store
        self._corrupt_checksum = corrupt_checksum
        self.requests: list[SandboxJobRequest] = []
        self.timeouts: list[int] = []

    def execute(self, request: SandboxJobRequest, *, timeout_seconds: int) -> str:
        self.requests.append(request)
        self.timeouts.append(timeout_seconds)
        result = SandboxJobResultEnvelope(
            request_id=request.request_id,
            bundle_sha256=("0" * 64 if self._corrupt_checksum else request.bundle_sha256),
            status=SandboxJobStatus.COMPLETED,
            check_result=CodeSandboxCheckResult(
                check=request.validation.check,
                targets=request.validation.targets,
                passed=True,
                exit_code=0,
                elapsed_ms=17,
                output="1 passed",
            ),
            worker_version="test-worker",
        )
        self._store.upload_bytes(
            request.result_object,
            result.model_dump_json().encode("utf-8"),
            "application/json",
        )
        return "projects/test/locations/asia-northeast3/jobs/sandbox/executions/1"


class SecretLeakingJobExecutor(SandboxJobExecutor):
    def execute(self, request: SandboxJobRequest, *, timeout_seconds: int) -> str:
        del request, timeout_seconds
        raise RuntimeError("provider failed with sk-ant-must-not-leak")


class FakeCloudOperation:
    def __init__(self, execution: run_v2.Execution) -> None:
        self._execution = execution
        self.timeouts: list[float] = []

    def result(self, *, timeout: float) -> object:
        self.timeouts.append(timeout)
        return self._execution


class FakeJobsClient:
    def __init__(self, operation: FakeCloudOperation) -> None:
        self._operation = operation
        self.requests: list[run_v2.RunJobRequest] = []

    def run_job(self, request: run_v2.RunJobRequest) -> FakeCloudOperation:
        self.requests.append(request)
        return self._operation


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    target = workspace / "tests" / "unit" / "poc" / "test_metric.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_metric() -> None:\n    assert True\n", encoding="utf-8")
    source = workspace / "src" / "defense_research_agent" / "poc" / "metric.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return workspace


def test_remote_runner_uploads_checksum_bound_bundle_and_returns_result(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    store = MemoryObjectStore()
    executor = CompletingJobExecutor(store)
    runner = GcpCloudRunJobValidationRunner(store, executor)
    validation = CodeSandboxValidation(
        check=CodeSandboxCheck.PYTEST,
        targets=["tests/unit/poc/test_metric.py"],
        timeout_seconds=20,
    )

    result = runner.run(workspace, validation)

    assert result.passed is True
    assert result.output == "1 passed"
    assert executor.timeouts == [50]
    request = executor.requests[0]
    assert request.bundle_sha256 == create_workspace_bundle(workspace).sha256
    assert request.bundle_object in store.objects
    assert request.result_object in store.objects
    assert f"sandbox-requests/{request.request_id}/request.json" in store.objects


def test_remote_runner_rejects_result_not_bound_to_uploaded_bundle(
    tmp_path: Path,
) -> None:
    store = MemoryObjectStore()
    runner = GcpCloudRunJobValidationRunner(
        store,
        CompletingJobExecutor(store, corrupt_checksum=True),
    )
    validation = CodeSandboxValidation(
        check=CodeSandboxCheck.PYTEST,
        targets=["tests/unit/poc/test_metric.py"],
    )

    with pytest.raises(SandboxValidationUnavailableError, match="checksum mismatch"):
        runner.run(_workspace(tmp_path), validation)


def test_remote_runner_sanitizes_unexpected_backend_failures(tmp_path: Path) -> None:
    runner = GcpCloudRunJobValidationRunner(
        MemoryObjectStore(),
        SecretLeakingJobExecutor(),
    )
    validation = CodeSandboxValidation(
        check=CodeSandboxCheck.PYTEST,
        targets=["tests/unit/poc/test_metric.py"],
    )

    with pytest.raises(SandboxValidationUnavailableError) as captured:
        runner.run(_workspace(tmp_path), validation)

    assert "RuntimeError" in str(captured.value)
    assert "sk-ant-must-not-leak" not in str(captured.value)


def test_workspace_bundle_is_deterministic_and_rejects_symlinks(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    first = create_workspace_bundle(workspace)
    second = create_workspace_bundle(workspace)

    assert first.payload == second.payload
    assert first.sha256 == second.sha256

    symlink = workspace / "tests" / "unit" / "poc" / "linked.py"
    symlink.symlink_to(workspace / "pyproject.toml")
    with pytest.raises(ValueError, match="symlink"):
        create_workspace_bundle(workspace)


def test_cloud_run_executor_uses_one_task_and_structured_environment_override() -> None:
    operation = FakeCloudOperation(
        run_v2.Execution(
            name=("projects/test/locations/asia-northeast3/jobs/sandbox/executions/execution-1"),
            succeeded_count=1,
        )
    )
    fake_client = FakeJobsClient(operation)
    executor = GcpCloudRunSandboxJobExecutor(
        "test",
        "asia-northeast3",
        "sandbox",
        client=cast(run_v2.JobsClient, fake_client),
    )
    request = SandboxJobRequest(
        request_id="sandbox-request",
        bundle_object="sandbox-requests/request/workspace.zip",
        result_object="sandbox-requests/request/result.json",
        bundle_sha256="1" * 64,
        bundle_size_bytes=100,
        validation=CodeSandboxValidation(
            check=CodeSandboxCheck.PYTEST,
            targets=["tests/unit/poc/test_metric.py"],
        ),
    )

    execution_name = executor.execute(request, timeout_seconds=60)

    assert execution_name.endswith("/executions/execution-1")
    submitted = fake_client.requests[0]
    assert submitted.name == "projects/test/locations/asia-northeast3/jobs/sandbox"
    assert submitted.overrides.task_count == 1
    assert submitted.overrides.timeout.seconds == 60
    override = submitted.overrides.container_overrides[0]
    assert override.name == "sandbox-worker"
    assert override.env[0].name == "SANDBOX_REQUEST_JSON"
    assert SandboxJobRequest.model_validate_json(override.env[0].value) == request
    assert operation.timeouts == [120]
