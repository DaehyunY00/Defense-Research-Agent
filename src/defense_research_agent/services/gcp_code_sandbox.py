"""GCS and Cloud Run Jobs backend for isolated sandbox validation."""

import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from google.cloud import run_v2
from pydantic import ValidationError

from defense_research_agent.domain import (
    CodeSandboxCheckResult,
    CodeSandboxValidation,
    SandboxJobRequest,
    SandboxJobResultEnvelope,
    SandboxJobStatus,
)
from defense_research_agent.services.code_sandbox import (
    SandboxValidationRunner,
    SandboxValidationUnavailableError,
)

_BUNDLE_ROOTS = ("src", "tests", "pyproject.toml")
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


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


class _CloudOperation(Protocol):
    def result(self, *, timeout: float) -> object: ...


@dataclass(frozen=True, slots=True)
class WorkspaceBundle:
    """Deterministic workspace archive and its integrity metadata."""

    payload: bytes
    sha256: str


class SandboxObjectStore(ABC):
    """Create-only input/result object boundary used by controller and worker."""

    @abstractmethod
    def upload_bytes(self, object_name: str, payload: bytes, content_type: str) -> None:
        """Create one immutable object and fail if it already exists."""

    @abstractmethod
    def download_bytes(self, object_name: str) -> bytes:
        """Download one object by exact name without listing the bucket."""


class SandboxJobExecutor(ABC):
    """Execute an already-deployed isolated job for one structured request."""

    @abstractmethod
    def execute(self, request: SandboxJobRequest, *, timeout_seconds: int) -> str:
        """Wait for the Cloud Run execution and return its resource name."""


class GcsSandboxObjectStore(SandboxObjectStore):
    """Google Cloud Storage adapter with create-only uploads."""

    def __init__(
        self,
        bucket_name: str,
        *,
        client: _StorageClient | None = None,
        request_timeout_seconds: int = 60,
    ) -> None:
        normalized_bucket = bucket_name.strip()
        if not normalized_bucket:
            raise ValueError("bucket_name must not be empty")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if client is None:
            from google.cloud import storage  # type: ignore[attr-defined]

            client = cast(_StorageClient, storage.Client())
        self._client = client
        self._bucket = self._client.bucket(normalized_bucket)
        self._request_timeout_seconds = request_timeout_seconds

    def upload_bytes(self, object_name: str, payload: bytes, content_type: str) -> None:
        """Upload with an object-generation precondition to prevent overwrites."""
        blob = self._bucket.blob(_validated_object_name(object_name))
        blob.upload_from_string(
            payload,
            content_type=content_type,
            if_generation_match=0,
            timeout=self._request_timeout_seconds,
        )

    def download_bytes(self, object_name: str) -> bytes:
        """Download the exact requested object."""
        blob = self._bucket.blob(_validated_object_name(object_name))
        payload = blob.download_as_bytes(timeout=self._request_timeout_seconds)
        if not isinstance(payload, bytes):
            raise TypeError("Cloud Storage returned a non-bytes payload")
        return payload


class GcpCloudRunSandboxJobExecutor(SandboxJobExecutor):
    """Cloud Run v2 Jobs adapter using per-execution environment overrides."""

    def __init__(
        self,
        project_id: str,
        region: str,
        job_name: str,
        *,
        container_name: str = "sandbox-worker",
        client: run_v2.JobsClient | None = None,
    ) -> None:
        segments = [project_id.strip(), region.strip(), job_name.strip(), container_name.strip()]
        if any(not segment for segment in segments):
            raise ValueError("Cloud Run project, region, job, and container names are required")
        self._job_resource = f"projects/{segments[0]}/locations/{segments[1]}/jobs/{segments[2]}"
        self._container_name = segments[3]
        self._client = client or run_v2.JobsClient()

    def execute(self, request: SandboxJobRequest, *, timeout_seconds: int) -> str:
        """Run one task and wait until the worker container has completed."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        override = run_v2.RunJobRequest.Overrides.ContainerOverride(
            name=self._container_name,
            env=[
                run_v2.EnvVar(
                    name="SANDBOX_REQUEST_JSON",
                    value=request.model_dump_json(),
                )
            ],
        )
        run_request = run_v2.RunJobRequest(
            name=self._job_resource,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[override],
                task_count=1,
                timeout={"seconds": timeout_seconds},
            ),
        )
        operation = cast(_CloudOperation, self._client.run_job(request=run_request))
        execution = cast(
            run_v2.Execution,
            operation.result(timeout=timeout_seconds + 60),
        )
        if execution.failed_count or execution.cancelled_count:
            raise RuntimeError(
                "Cloud Run sandbox execution did not complete successfully "
                f"(failed={execution.failed_count}, cancelled={execution.cancelled_count})"
            )
        return execution.name


class GcpCloudRunJobValidationRunner(SandboxValidationRunner):
    """Bundle a workspace and delegate fixed validation to an isolated GCP job."""

    def __init__(
        self,
        object_store: SandboxObjectStore,
        job_executor: SandboxJobExecutor,
        *,
        object_prefix: str = "sandbox-requests",
        max_bundle_bytes: int = 10_000_000,
        worker_overhead_seconds: int = 30,
    ) -> None:
        if max_bundle_bytes <= 0:
            raise ValueError("max_bundle_bytes must be positive")
        if worker_overhead_seconds <= 0:
            raise ValueError("worker_overhead_seconds must be positive")
        normalized_prefix = _validated_object_name(object_prefix.strip("/"))
        self._object_store = object_store
        self._job_executor = job_executor
        self._object_prefix = normalized_prefix
        self._max_bundle_bytes = max_bundle_bytes
        self._worker_overhead_seconds = worker_overhead_seconds

    def run(
        self,
        workspace: Path,
        validation: CodeSandboxValidation,
    ) -> CodeSandboxCheckResult:
        """Submit one checksum-bound request and validate the structured response."""
        try:
            bundle = create_workspace_bundle(workspace)
            if len(bundle.payload) > self._max_bundle_bytes:
                raise ValueError("workspace bundle exceeds the configured byte limit")
            request_id = f"sandbox-{uuid4().hex}"
            request_prefix = f"{self._object_prefix}/{request_id}"
            request = SandboxJobRequest(
                request_id=request_id,
                bundle_object=f"{request_prefix}/workspace.zip",
                result_object=f"{request_prefix}/result.json",
                bundle_sha256=bundle.sha256,
                bundle_size_bytes=len(bundle.payload),
                validation=validation,
            )
            self._object_store.upload_bytes(
                request.bundle_object,
                bundle.payload,
                "application/zip",
            )
            self._object_store.upload_bytes(
                f"{request_prefix}/request.json",
                request.model_dump_json(indent=2).encode("utf-8"),
                "application/json",
            )
            self._job_executor.execute(
                request,
                timeout_seconds=validation.timeout_seconds + self._worker_overhead_seconds,
            )
            result_payload = self._object_store.download_bytes(request.result_object)
            if len(result_payload) > 100_000:
                raise ValueError("sandbox result exceeds the configured byte limit")
            result = SandboxJobResultEnvelope.model_validate_json(result_payload)
            _validate_result_binding(request, result)
            if result.status is not SandboxJobStatus.COMPLETED:
                raise SandboxValidationUnavailableError(
                    f"remote sandbox {result.status.value}: "
                    f"{result.failure_code or 'unknown_worker_failure'}"
                )
            if result.check_result is None:
                raise ValueError("completed sandbox result omitted check_result")
            return result.check_result
        except SandboxValidationUnavailableError:
            raise
        except ValidationError as error:
            raise SandboxValidationUnavailableError(
                "remote sandbox backend returned an invalid result"
            ) from error
        except ValueError as error:
            raise SandboxValidationUnavailableError(
                f"remote sandbox backend failed: {type(error).__name__}: {str(error)[:300]}"
            ) from error
        except Exception as error:
            raise SandboxValidationUnavailableError(
                f"remote sandbox backend failed ({type(error).__name__})"
            ) from error


def create_workspace_bundle(workspace: Path) -> WorkspaceBundle:
    """Create a deterministic ZIP containing only the copied project inputs."""
    resolved_workspace = workspace.resolve()
    if not resolved_workspace.is_dir():
        raise ValueError("workspace must be an existing directory")

    candidates: list[tuple[str, Path]] = []
    for root_name in _BUNDLE_ROOTS:
        root = resolved_workspace / root_name
        if not root.exists():
            continue
        if root.is_symlink():
            raise ValueError(f"workspace bundle cannot contain symlink: {root_name}")
        if root.is_file():
            candidates.append((root_name, root))
            continue
        for candidate in sorted(root.rglob("*")):
            relative_name = candidate.relative_to(resolved_workspace).as_posix()
            if candidate.is_symlink():
                raise ValueError(f"workspace bundle cannot contain symlink: {relative_name}")
            if candidate.is_file():
                candidates.append((relative_name, candidate))

    archive = BytesIO()
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as zip_file:
        for relative_name, source in sorted(candidates):
            info = ZipInfo(relative_name, date_time=_FIXED_ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.create_system = 3
            zip_file.writestr(info, source.read_bytes())
    payload = archive.getvalue()
    if not payload:
        raise ValueError("workspace bundle is empty")
    return WorkspaceBundle(payload=payload, sha256=sha256(payload).hexdigest())


def _validate_result_binding(
    request: SandboxJobRequest,
    result: SandboxJobResultEnvelope,
) -> None:
    if result.request_id != request.request_id:
        raise ValueError("sandbox result request_id mismatch")
    if result.bundle_sha256 != request.bundle_sha256:
        raise ValueError("sandbox result bundle checksum mismatch")
    if result.check_result is not None:
        if result.check_result.check is not request.validation.check:
            raise ValueError("sandbox result check mismatch")
        if result.check_result.targets != request.validation.targets:
            raise ValueError("sandbox result targets mismatch")


def _validated_object_name(object_name: str) -> str:
    normalized = object_name.strip()
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("sandbox object name must be a safe relative object path")
    return normalized
