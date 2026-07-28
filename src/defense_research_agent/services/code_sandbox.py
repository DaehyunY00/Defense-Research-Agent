"""Ephemeral code-patch validation without applying changes to the source tree."""

import difflib
import shutil
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from defense_research_agent.domain import (
    ArtifactKind,
    CodeFileChange,
    CodeFileOperation,
    CodeSandboxCheck,
    CodeSandboxCheckResult,
    CodeSandboxResult,
    CodeSandboxStatus,
    CodeSandboxValidation,
    ProposedArtifact,
    ResearchBrief,
    ResearchTask,
)

_DEFAULT_ALLOWED_PREFIXES = (
    "src/defense_research_agent/poc",
    "tests/unit/poc",
)


class SandboxValidationUnavailableError(RuntimeError):
    """Raised when a requested check needs a stronger isolation backend."""


class SandboxValidationRunner(ABC):
    """Backend boundary for fixed validations inside an isolated workspace."""

    @abstractmethod
    def run(
        self,
        workspace: Path,
        validation: CodeSandboxValidation,
    ) -> CodeSandboxCheckResult:
        """Run one allow-listed validation without accepting command strings."""


class StaticSandboxValidationRunner(SandboxValidationRunner):
    """Safely compile Python source without executing model-generated code."""

    def run(
        self,
        workspace: Path,
        validation: CodeSandboxValidation,
    ) -> CodeSandboxCheckResult:
        """Support syntax compilation; require a remote isolated runner for pytest."""
        if validation.check is CodeSandboxCheck.PYTEST:
            raise SandboxValidationUnavailableError(
                "pytest requires an isolated process or container runner"
            )

        started = time.monotonic()
        output_lines: list[str] = []
        passed = True
        for target_name in validation.targets:
            target = _resolved_workspace_target(workspace, target_name)
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
        elapsed_ms = int((time.monotonic() - started) * 1_000)
        return CodeSandboxCheckResult(
            check=validation.check,
            targets=list(validation.targets),
            passed=passed,
            exit_code=0 if passed else 1,
            elapsed_ms=elapsed_ms,
            output="\n".join(output_lines)[:8_000],
        )


class CodeSandboxExecutor:
    """Validate structured code patches in a disposable copied workspace."""

    def __init__(
        self,
        source_root: Path,
        sandbox_root: Path,
        *,
        validation_runner: SandboxValidationRunner | None = None,
        allowed_path_prefixes: Sequence[str] = _DEFAULT_ALLOWED_PREFIXES,
        max_total_change_bytes: int = 500_000,
    ) -> None:
        resolved_source = source_root.resolve()
        resolved_sandbox = sandbox_root.resolve()
        if not resolved_source.is_dir():
            raise ValueError("source_root must be an existing directory")
        if resolved_sandbox == resolved_source or resolved_sandbox.is_relative_to(resolved_source):
            raise ValueError("sandbox_root must be outside source_root")
        if max_total_change_bytes <= 0:
            raise ValueError("max_total_change_bytes must be positive")
        prefixes = tuple(dict.fromkeys(prefix.strip("/") for prefix in allowed_path_prefixes))
        if not prefixes or any(not prefix for prefix in prefixes):
            raise ValueError("allowed_path_prefixes must contain safe relative prefixes")
        self._source_root = resolved_source
        self._sandbox_root = resolved_sandbox
        self._sandbox_root.mkdir(parents=True, exist_ok=True)
        self._validation_runner = validation_runner or StaticSandboxValidationRunner()
        self._allowed_path_prefixes = prefixes
        self._max_total_change_bytes = max_total_change_bytes

    def execute(
        self,
        brief: ResearchBrief,
        task: ResearchTask,
        artifact: ProposedArtifact,
    ) -> CodeSandboxResult:
        """Apply one proposal only to a temporary copy and preserve its diff."""
        preflight_failure = self._preflight_failure(artifact)
        if preflight_failure is not None:
            code, message = preflight_failure
            return self._blocked_result(brief, task, artifact, code, message)

        with TemporaryDirectory(
            prefix="defense-research-code-",
            dir=self._sandbox_root,
        ) as temporary_directory:
            workspace = Path(temporary_directory) / "workspace"
            self._copy_project(workspace)
            before_hashes = self._source_hashes(artifact.code_changes)
            try:
                changed_paths, unified_diff = self._apply_changes(
                    workspace,
                    artifact.code_changes,
                )
                self._validate_targets(artifact, changed_paths)
            except OSError as error:
                return self._blocked_result(
                    brief,
                    task,
                    artifact,
                    "sandbox_io_failure",
                    f"{type(error).__name__}: sandbox workspace operation failed",
                )
            except (UnicodeError, ValueError) as error:
                return self._blocked_result(
                    brief,
                    task,
                    artifact,
                    "sandbox_policy_block",
                    f"{type(error).__name__}: {str(error)[:400]}",
                )

            check_results: list[CodeSandboxCheckResult] = []
            try:
                for validation in artifact.sandbox_validations:
                    result = self._validation_runner.run(workspace, validation)
                    check_results.append(result)
                    if not result.passed:
                        return CodeSandboxResult(
                            project_id=brief.project_id,
                            task_id=task.task_id,
                            artifact_id=artifact.artifact_id,
                            status=CodeSandboxStatus.FAILED,
                            changed_paths=changed_paths,
                            unified_diff=unified_diff,
                            check_results=check_results,
                            failure_code="validation_failed",
                            failure_message=(
                                f"{validation.check.value} failed for "
                                f"{', '.join(validation.targets)}"
                            ),
                        )
            except SandboxValidationUnavailableError as error:
                return CodeSandboxResult(
                    project_id=brief.project_id,
                    task_id=task.task_id,
                    artifact_id=artifact.artifact_id,
                    status=CodeSandboxStatus.BLOCKED,
                    changed_paths=changed_paths,
                    unified_diff=unified_diff,
                    check_results=check_results,
                    failure_code="isolation_backend_required",
                    failure_message=str(error),
                )
            finally:
                self._assert_source_unchanged(before_hashes)

            return CodeSandboxResult(
                project_id=brief.project_id,
                task_id=task.task_id,
                artifact_id=artifact.artifact_id,
                status=CodeSandboxStatus.PASSED,
                changed_paths=changed_paths,
                unified_diff=unified_diff,
                check_results=check_results,
            )

    def _preflight_failure(
        self,
        artifact: ProposedArtifact,
    ) -> tuple[str, str] | None:
        if artifact.kind is not ArtifactKind.CODE_PATCH:
            return "unsupported_artifact_kind", "code sandbox accepts only code_patch artifacts"
        if not artifact.code_changes:
            return "code_changes_required", "code patch does not contain code_changes"
        if not artifact.sandbox_validations:
            return "validation_required", "code patch requires at least one sandbox validation"
        total_bytes = sum(len(change.content.encode("utf-8")) for change in artifact.code_changes)
        if total_bytes > self._max_total_change_bytes:
            return "change_size_exceeded", "code patch exceeds the configured byte limit"
        duplicate_paths = [
            path
            for path in {change.relative_path for change in artifact.code_changes}
            if sum(item.relative_path == path for item in artifact.code_changes) > 1
        ]
        if duplicate_paths:
            return "duplicate_change_path", f"duplicate code change paths: {duplicate_paths}"
        denied_paths = [
            change.relative_path
            for change in artifact.code_changes
            if not self._is_allowed_path(change.relative_path)
        ]
        if denied_paths:
            return "path_not_allowed", f"code changes target disallowed paths: {denied_paths}"
        return None

    def _copy_project(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=False)
        for directory_name in ("src", "tests"):
            source = self._source_root / directory_name
            if source.is_dir():
                shutil.copytree(source, workspace / directory_name, symlinks=True)
        pyproject = self._source_root / "pyproject.toml"
        if pyproject.is_file():
            shutil.copy2(pyproject, workspace / "pyproject.toml")

    def _apply_changes(
        self,
        workspace: Path,
        changes: Sequence[CodeFileChange],
    ) -> tuple[list[str], str]:
        changed_paths: list[str] = []
        diffs: list[str] = []
        for change in changes:
            target = _resolved_workspace_target(workspace, change.relative_path)
            _reject_symlink_path(workspace, target)
            before = ""
            if change.operation is CodeFileOperation.CREATE:
                if target.exists():
                    raise ValueError(f"create target already exists: {change.relative_path}")
            else:
                if not target.is_file():
                    raise ValueError(f"replace target is not a file: {change.relative_path}")
                before = target.read_text(encoding="utf-8")
                actual_checksum = sha256(target.read_bytes()).hexdigest()
                if actual_checksum != change.expected_sha256:
                    raise ValueError(f"replace checksum mismatch: {change.relative_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8")
            changed_paths.append(change.relative_path)
            diffs.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    change.content.splitlines(keepends=True),
                    fromfile=(
                        f"a/{change.relative_path}"
                        if change.operation is CodeFileOperation.REPLACE
                        else "/dev/null"
                    ),
                    tofile=f"b/{change.relative_path}",
                )
            )
        return changed_paths, "".join(diffs)[:50_000]

    def _validate_targets(
        self,
        artifact: ProposedArtifact,
        changed_paths: Sequence[str],
    ) -> None:
        changed_path_set = set(changed_paths)
        for validation in artifact.sandbox_validations:
            unknown_targets = set(validation.targets) - changed_path_set
            if unknown_targets:
                raise ValueError(
                    f"sandbox validation targets unchanged files: {sorted(unknown_targets)}"
                )
            if validation.check is CodeSandboxCheck.PYTEST and any(
                not target.startswith("tests/") for target in validation.targets
            ):
                raise ValueError("pytest targets must be under tests/")

    def _source_hashes(
        self,
        changes: Sequence[CodeFileChange],
    ) -> dict[str, str | None]:
        hashes: dict[str, str | None] = {}
        for change in changes:
            source_path = self._source_root / change.relative_path
            hashes[change.relative_path] = (
                sha256(source_path.read_bytes()).hexdigest() if source_path.is_file() else None
            )
        return hashes

    def _assert_source_unchanged(self, before_hashes: dict[str, str | None]) -> None:
        after_hashes = {
            relative_path: (
                sha256((self._source_root / relative_path).read_bytes()).hexdigest()
                if (self._source_root / relative_path).is_file()
                else None
            )
            for relative_path in before_hashes
        }
        if after_hashes != before_hashes:
            raise RuntimeError("source project changed during sandbox validation")

    def _is_allowed_path(self, relative_path: str) -> bool:
        return any(
            relative_path == prefix or relative_path.startswith(f"{prefix}/")
            for prefix in self._allowed_path_prefixes
        )

    @staticmethod
    def _blocked_result(
        brief: ResearchBrief,
        task: ResearchTask,
        artifact: ProposedArtifact,
        code: str,
        message: str,
    ) -> CodeSandboxResult:
        return CodeSandboxResult(
            project_id=brief.project_id,
            task_id=task.task_id,
            artifact_id=artifact.artifact_id,
            status=CodeSandboxStatus.BLOCKED,
            failure_code=code,
            failure_message=message,
        )


def _resolved_workspace_target(workspace: Path, relative_path: str) -> Path:
    resolved_workspace = workspace.resolve()
    target = (workspace / relative_path).resolve()
    if not target.is_relative_to(resolved_workspace):
        raise ValueError("sandbox target escapes the temporary workspace")
    return target


def _reject_symlink_path(workspace: Path, target: Path) -> None:
    current = target
    resolved_workspace = workspace.resolve()
    while current != resolved_workspace:
        if current.is_symlink():
            raise ValueError("sandbox changes cannot target symlinks")
        current = current.parent
