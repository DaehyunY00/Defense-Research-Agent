"""Cloud Run worker-side extraction and fixed validation execution."""

import os
import stat
import subprocess
import sys
import time
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile, ZipInfo

from defense_research_agent import __version__
from defense_research_agent.domain import (
    CodeSandboxCheck,
    CodeSandboxCheckResult,
    CodeSandboxValidation,
    SandboxJobRequest,
    SandboxJobResultEnvelope,
    SandboxJobStatus,
)
from defense_research_agent.services.gcp_code_sandbox import GcsSandboxObjectStore

_MAX_EXTRACTED_BYTES = 25_000_000
_ALLOWED_BUNDLE_ROOTS = {"src", "tests", "pyproject.toml"}


class SandboxBundleRejectedError(ValueError):
    """Raised when a bundle violates worker-side archive policy."""


def extract_workspace_bundle(payload: bytes, destination: Path) -> None:
    """Extract a bounded regular-file-only ZIP without using extractall."""
    destination.mkdir(parents=True, exist_ok=False)
    resolved_destination = destination.resolve()
    total_size = 0
    seen_names: set[str] = set()
    try:
        with ZipFile(BytesIO(payload), mode="r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative_path = _validated_archive_member(info)
                relative_name = relative_path.as_posix()
                if relative_name in seen_names:
                    raise SandboxBundleRejectedError("bundle contains duplicate file names")
                seen_names.add(relative_name)
                total_size += info.file_size
                if total_size > _MAX_EXTRACTED_BYTES:
                    raise SandboxBundleRejectedError("bundle expands beyond the byte limit")
                target = (resolved_destination / relative_name).resolve()
                if not target.is_relative_to(resolved_destination):
                    raise SandboxBundleRejectedError("bundle member escapes the workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, mode="r") as source:
                    content = source.read(_MAX_EXTRACTED_BYTES + 1)
                if len(content) != info.file_size:
                    raise SandboxBundleRejectedError("bundle member size is inconsistent")
                target.write_bytes(content)
                target.chmod(0o644)
    except BadZipFile as error:
        raise SandboxBundleRejectedError("bundle is not a valid ZIP archive") from error
    if not seen_names:
        raise SandboxBundleRejectedError("bundle contains no files")


def run_worker_validation(
    workspace: Path,
    validation: CodeSandboxValidation,
) -> CodeSandboxCheckResult:
    """Run one fixed validation in the already-isolated worker container."""
    started = time.monotonic()
    if validation.check is CodeSandboxCheck.PYTHON_COMPILE:
        output_lines: list[str] = []
        passed = True
        for target_name in validation.targets:
            target = _resolved_target(workspace, target_name)
            if target.suffix != ".py" or not target.is_file():
                passed = False
                output_lines.append(f"{target_name}: expected an existing Python file")
                continue
            try:
                compile(target.read_text(encoding="utf-8"), target_name, "exec")
            except (SyntaxError, UnicodeError) as error:
                passed = False
                output_lines.append(f"{target_name}: {type(error).__name__}: {error}")
            else:
                output_lines.append(f"{target_name}: syntax ok")
        return CodeSandboxCheckResult(
            check=validation.check,
            targets=list(validation.targets),
            passed=passed,
            exit_code=0 if passed else 1,
            elapsed_ms=_elapsed_ms(started),
            output="\n".join(output_lines)[:8_000],
        )

    _validate_pytest_targets(workspace, validation.targets)
    sandbox_home = workspace / ".sandbox-home"
    sandbox_tmp = workspace / ".sandbox-tmp"
    sandbox_home.mkdir(mode=0o700)
    sandbox_tmp.mkdir(mode=0o700)
    environment = {
        "HOME": str(sandbox_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(workspace / "src"),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TMPDIR": str(sandbox_tmp),
    }
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--cache-clear",
        "-q",
        *validation.targets,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=validation.timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        output = _timeout_output(error)
        return CodeSandboxCheckResult(
            check=validation.check,
            targets=list(validation.targets),
            passed=False,
            exit_code=None,
            elapsed_ms=_elapsed_ms(started),
            output=f"pytest timed out after {validation.timeout_seconds}s\n{output}"[:8_000],
        )
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    return CodeSandboxCheckResult(
        check=validation.check,
        targets=list(validation.targets),
        passed=completed.returncode == 0,
        exit_code=completed.returncode,
        elapsed_ms=_elapsed_ms(started),
        output=output[:8_000],
    )


def run_worker_from_environment() -> int:
    """Process one request from static bucket and per-execution request settings."""
    bucket_name = os.environ.get("SANDBOX_BUCKET", "").strip()
    request_json = os.environ.get("SANDBOX_REQUEST_JSON", "")
    if not bucket_name or not request_json:
        print("sandbox worker configuration is incomplete", file=sys.stderr)
        return 2
    try:
        request = SandboxJobRequest.model_validate_json(request_json)
    except ValueError as error:
        print(f"invalid sandbox request: {type(error).__name__}", file=sys.stderr)
        return 2

    store = GcsSandboxObjectStore(bucket_name)
    try:
        bundle = store.download_bytes(request.bundle_object)
        if len(bundle) != request.bundle_size_bytes:
            raise SandboxBundleRejectedError("bundle size does not match request")
        if sha256(bundle).hexdigest() != request.bundle_sha256:
            raise SandboxBundleRejectedError("bundle checksum does not match request")
        with TemporaryDirectory(prefix="defense-research-worker-") as temporary_directory:
            workspace = Path(temporary_directory) / "workspace"
            extract_workspace_bundle(bundle, workspace)
            check_result = run_worker_validation(workspace, request.validation)
        result = SandboxJobResultEnvelope(
            request_id=request.request_id,
            bundle_sha256=request.bundle_sha256,
            status=SandboxJobStatus.COMPLETED,
            check_result=check_result,
            worker_version=__version__,
        )
    except SandboxBundleRejectedError as error:
        result = _worker_failure(
            request,
            SandboxJobStatus.REJECTED,
            "bundle_rejected",
            str(error),
        )
    except Exception as error:
        result = _worker_failure(
            request,
            SandboxJobStatus.FAILED,
            "worker_failure",
            f"{type(error).__name__}: sandbox worker failed",
        )

    try:
        store.upload_bytes(
            request.result_object,
            result.model_dump_json(indent=2).encode("utf-8"),
            "application/json",
        )
    except Exception as error:
        print(
            f"sandbox result upload failed for {request.request_id}: {type(error).__name__}",
            file=sys.stderr,
        )
        return 3
    print(f"sandbox request completed: {request.request_id}")
    return 0


def _validated_archive_member(info: ZipInfo) -> PurePosixPath:
    name = info.filename
    path = PurePosixPath(name)
    mode = info.external_attr >> 16
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] not in _ALLOWED_BUNDLE_ROOTS
        or info.flag_bits & 0x1
        or stat.S_ISLNK(mode)
        or (mode and not stat.S_ISREG(mode))
    ):
        raise SandboxBundleRejectedError("bundle contains an unsafe member")
    return path


def _resolved_target(workspace: Path, target_name: str) -> Path:
    resolved_workspace = workspace.resolve()
    target = (workspace / target_name).resolve()
    if not target.is_relative_to(resolved_workspace):
        raise SandboxBundleRejectedError("validation target escapes workspace")
    return target


def _validate_pytest_targets(workspace: Path, targets: list[str]) -> None:
    for target_name in targets:
        target = _resolved_target(workspace, target_name)
        if not target_name.startswith("tests/") or target.suffix != ".py" or not target.is_file():
            raise SandboxBundleRejectedError(
                "pytest accepts only existing Python files under tests/"
            )


def _worker_failure(
    request: SandboxJobRequest,
    status: SandboxJobStatus,
    code: str,
    message: str,
) -> SandboxJobResultEnvelope:
    return SandboxJobResultEnvelope(
        request_id=request.request_id,
        bundle_sha256=request.bundle_sha256,
        status=status,
        failure_code=code,
        failure_message=message[:500],
        worker_version=__version__,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1_000)


def _timeout_output(error: subprocess.TimeoutExpired) -> str:
    parts: list[str] = []
    for value in (error.stdout, error.stderr):
        if isinstance(value, bytes):
            parts.append(value.decode("utf-8", errors="replace"))
    return "\n".join(parts)
