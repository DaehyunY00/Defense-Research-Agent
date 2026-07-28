"""Unit tests for health reporting and CLI rendering."""

import json
from typing import cast

import pytest

from defense_research_agent.cli import main
from defense_research_agent.services.health import (
    PACKAGE_NAME,
    PYTHON_REQUIREMENT,
    build_health_report,
)


def test_build_health_report_uses_supported_python() -> None:
    report = build_health_report()

    assert report.status == "ok"
    assert report.package == PACKAGE_NAME
    assert report.version
    assert report.python.startswith("3.12.")
    assert report.python_requirement == PYTHON_REQUIREMENT


def test_health_cli_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["health", "--format", "json"])

    captured = capsys.readouterr()
    payload = cast(dict[str, object], json.loads(captured.out))

    assert exit_code == 0
    assert captured.err == ""
    assert payload["status"] == "ok"
    assert payload["package"] == PACKAGE_NAME
