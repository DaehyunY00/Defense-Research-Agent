"""Tests for ephemeral code patch validation and source-tree protection."""

from pathlib import Path

from defense_research_agent.domain import (
    ArtifactKind,
    CodeFileChange,
    CodeFileOperation,
    CodeSandboxCheck,
    CodeSandboxStatus,
    CodeSandboxValidation,
    ProposedArtifact,
    ResearchBrief,
    ResearchRole,
    ResearchTask,
)
from defense_research_agent.services import CodeSandboxExecutor


def _brief() -> ResearchBrief:
    return ResearchBrief(
        project_id="project:sandbox",
        question="정책 지표 PoC를 만들 수 있는가?",
        objective="코드 변경안을 격리 검증한다.",
        deliverables=["검증된 코드 제안"],
    )


def _task() -> ResearchTask:
    return ResearchTask(
        task_id="task:developer",
        role=ResearchRole.DEVELOPER_RESEARCHER,
        title="코드 PoC",
        instructions="임시 작업공간에서만 코드를 검증한다.",
        expected_output="diff와 검증 결과",
    )


def _source_root(tmp_path: Path) -> Path:
    source_root = tmp_path / "source"
    (source_root / "src" / "defense_research_agent").mkdir(parents=True)
    (source_root / "tests" / "unit").mkdir(parents=True)
    (source_root / "pyproject.toml").write_text(
        "[project]\nname='sandbox-fixture'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    return source_root


def _artifact(
    changes: list[CodeFileChange],
    validations: list[CodeSandboxValidation],
) -> ProposedArtifact:
    return ProposedArtifact(
        artifact_id="artifact:poc",
        kind=ArtifactKind.CODE_PATCH,
        title="정책 지표 PoC",
        summary="격리 검증할 최소 코드 변경안",
        validation_commands=["touch /tmp/must-never-run"],
        code_changes=changes,
        sandbox_validations=validations,
    )


def test_syntax_validation_passes_in_ephemeral_copy_without_source_mutation(
    tmp_path: Path,
) -> None:
    source_root = _source_root(tmp_path)
    target_path = "src/defense_research_agent/poc/metric.py"
    artifact = _artifact(
        [
            CodeFileChange(
                relative_path=target_path,
                operation=CodeFileOperation.CREATE,
                content="def score(value: int) -> int:\n    return value\n",
            )
        ],
        [
            CodeSandboxValidation(
                check=CodeSandboxCheck.PYTHON_COMPILE,
                targets=[target_path],
            )
        ],
    )
    executor = CodeSandboxExecutor(
        source_root,
        tmp_path / "sandboxes",
    )

    result = executor.execute(_brief(), _task(), artifact)

    assert result.status is CodeSandboxStatus.PASSED
    assert result.changed_paths == [target_path]
    assert "def score" in result.unified_diff
    assert result.check_results[0].passed is True
    assert result.applied_to_source is False
    assert not (source_root / target_path).exists()
    assert not Path("/tmp/must-never-run").exists()


def test_disallowed_path_and_replace_checksum_mismatch_are_blocked(
    tmp_path: Path,
) -> None:
    source_root = _source_root(tmp_path)
    existing = source_root / "src" / "defense_research_agent" / "poc" / "existing.py"
    existing.parent.mkdir()
    existing.write_text("VALUE = 1\n", encoding="utf-8")
    executor = CodeSandboxExecutor(source_root, tmp_path / "sandboxes")
    disallowed = _artifact(
        [
            CodeFileChange(
                relative_path="src/defense_research_agent/core.py",
                operation=CodeFileOperation.CREATE,
                content="VALUE = 2\n",
            )
        ],
        [
            CodeSandboxValidation(
                check=CodeSandboxCheck.PYTHON_COMPILE,
                targets=["src/defense_research_agent/core.py"],
            )
        ],
    )
    checksum_mismatch = _artifact(
        [
            CodeFileChange(
                relative_path="src/defense_research_agent/poc/existing.py",
                operation=CodeFileOperation.REPLACE,
                content="VALUE = 2\n",
                expected_sha256="0" * 64,
            )
        ],
        [
            CodeSandboxValidation(
                check=CodeSandboxCheck.PYTHON_COMPILE,
                targets=["src/defense_research_agent/poc/existing.py"],
            )
        ],
    )

    denied_result = executor.execute(_brief(), _task(), disallowed)
    checksum_result = executor.execute(_brief(), _task(), checksum_mismatch)

    assert denied_result.status is CodeSandboxStatus.BLOCKED
    assert denied_result.failure_code == "path_not_allowed"
    assert checksum_result.status is CodeSandboxStatus.BLOCKED
    assert checksum_result.failure_code == "sandbox_policy_block"
    assert existing.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_python_syntax_failure_is_reported_without_source_mutation(
    tmp_path: Path,
) -> None:
    source_root = _source_root(tmp_path)
    target_path = "src/defense_research_agent/poc/broken.py"
    artifact = _artifact(
        [
            CodeFileChange(
                relative_path=target_path,
                operation=CodeFileOperation.CREATE,
                content="def broken(:\n    pass\n",
            )
        ],
        [
            CodeSandboxValidation(
                check=CodeSandboxCheck.PYTHON_COMPILE,
                targets=[target_path],
            )
        ],
    )
    executor = CodeSandboxExecutor(source_root, tmp_path / "sandboxes")

    result = executor.execute(_brief(), _task(), artifact)

    assert result.status is CodeSandboxStatus.FAILED
    assert result.failure_code == "validation_failed"
    assert "SyntaxError" in result.check_results[0].output
    assert not (source_root / target_path).exists()


def test_pytest_is_blocked_without_an_isolated_process_backend(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    test_path = "tests/unit/poc/test_example.py"
    artifact = _artifact(
        [
            CodeFileChange(
                relative_path=test_path,
                operation=CodeFileOperation.CREATE,
                content="def test_example() -> None:\n    assert True\n",
            )
        ],
        [
            CodeSandboxValidation(
                check=CodeSandboxCheck.PYTEST,
                targets=[test_path],
            )
        ],
    )
    executor = CodeSandboxExecutor(source_root, tmp_path / "sandboxes")

    result = executor.execute(_brief(), _task(), artifact)

    assert result.status is CodeSandboxStatus.BLOCKED
    assert result.failure_code == "isolation_backend_required"
    assert result.changed_paths == [test_path]
    assert not (source_root / test_path).exists()
