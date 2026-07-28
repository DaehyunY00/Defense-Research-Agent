"""Tests for the isolated worker's archive and fixed-command boundaries."""

import stat
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from defense_research_agent.domain import CodeSandboxCheck, CodeSandboxValidation
from defense_research_agent.services.gcp_code_sandbox import create_workspace_bundle
from defense_research_agent.services.sandbox_worker import (
    SandboxBundleRejectedError,
    extract_workspace_bundle,
    run_worker_validation,
)


def _workspace(tmp_path: Path, test_content: str) -> Path:
    workspace = tmp_path / "source"
    source = workspace / "src" / "defense_research_agent" / "poc" / "metric.py"
    source.parent.mkdir(parents=True)
    (workspace / "src" / "defense_research_agent" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    source.write_text(
        "def double(value: int) -> int:\n    return value * 2\n",
        encoding="utf-8",
    )
    test = workspace / "tests" / "unit" / "poc" / "test_metric.py"
    test.parent.mkdir(parents=True)
    test.write_text(test_content, encoding="utf-8")
    (workspace / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath=['src']\n",
        encoding="utf-8",
    )
    return workspace


def test_worker_extracts_controller_bundle_and_runs_fixed_pytest(tmp_path: Path) -> None:
    source = _workspace(
        tmp_path,
        (
            "from defense_research_agent.poc.metric import double\n\n"
            "def test_double() -> None:\n"
            "    assert double(3) == 6\n"
        ),
    )
    bundle = create_workspace_bundle(source)
    extracted = tmp_path / "extracted"
    extract_workspace_bundle(bundle.payload, extracted)
    validation = CodeSandboxValidation(
        check=CodeSandboxCheck.PYTEST,
        targets=["tests/unit/poc/test_metric.py"],
        timeout_seconds=10,
    )

    result = run_worker_validation(extracted, validation)

    assert result.passed is True
    assert result.exit_code == 0
    assert "1 passed" in result.output


def test_worker_reports_pytest_assertion_failure(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        "def test_failure() -> None:\n    assert False\n",
    )
    validation = CodeSandboxValidation(
        check=CodeSandboxCheck.PYTEST,
        targets=["tests/unit/poc/test_metric.py"],
        timeout_seconds=10,
    )

    result = run_worker_validation(workspace, validation)

    assert result.passed is False
    assert result.exit_code == 1
    assert "failed" in result.output


def test_worker_rejects_traversal_and_symlink_archive_members(tmp_path: Path) -> None:
    traversal = _archive_with_member("../../escape.py", b"VALUE = 1\n")
    with pytest.raises(SandboxBundleRejectedError, match="unsafe member"):
        extract_workspace_bundle(traversal, tmp_path / "traversal")

    symlink_info = ZipInfo("tests/unit/poc/link.py")
    symlink_info.create_system = 3
    symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink = _archive_with_info(symlink_info, b"/etc/passwd")
    with pytest.raises(SandboxBundleRejectedError, match="unsafe member"):
        extract_workspace_bundle(symlink, tmp_path / "symlink")


def test_worker_rejects_pytest_target_outside_tests(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "def test_ok() -> None:\n    assert True\n")
    validation = CodeSandboxValidation(
        check=CodeSandboxCheck.PYTEST,
        targets=["src/defense_research_agent/poc/metric.py"],
    )

    with pytest.raises(SandboxBundleRejectedError, match="under tests"):
        run_worker_validation(workspace, validation)


def _archive_with_member(name: str, content: bytes) -> bytes:
    info = ZipInfo(name)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return _archive_with_info(info, content)


def _archive_with_info(info: ZipInfo, content: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(info, content)
    return output.getvalue()
