"""Integration tests for the installed module entry point."""

import json
import os
import subprocess
import sys
from typing import cast


def test_module_health_command() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "defense_research_agent", "health", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = cast(dict[str, object], json.loads(completed.stdout))

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert payload["status"] == "ok"
    assert str(payload["python"]).startswith("3.12.")


def test_claude_config_requires_only_key_and_never_prints_it() -> None:
    environment = dict(os.environ)
    environment["ANTHROPIC_API_KEY"] = "sk-ant-cli-secret"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent",
            "claude-config",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    payload = cast(dict[str, object], json.loads(completed.stdout))

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert payload["status"] == "ok"
    assert payload["api_key_configured"] is True
    assert "sk-ant-cli-secret" not in completed.stdout
    role_models = cast(dict[str, str], payload["role_models"])
    assert role_models["main_researcher"] == "claude-opus-5"
    assert role_models["literature_researcher"] == "claude-haiku-4-5"


def test_claude_config_reports_missing_key_without_stack_trace() -> None:
    environment = dict(os.environ)
    environment.pop("ANTHROPIC_API_KEY", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent",
            "claude-config",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    payload = cast(dict[str, object], json.loads(completed.stdout))

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert payload == {
        "reason": "invalid_or_missing_claude_configuration",
        "status": "error",
    }
