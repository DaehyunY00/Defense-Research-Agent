"""Command-line entry point."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from typing import cast

from pydantic import ValidationError

from defense_research_agent.agents import AnthropicRuntimeSettings
from defense_research_agent.services.health import HealthReport, build_health_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="defense-research-agent",
        description="Defense research agent command-line interface.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    health_parser = subparsers.add_parser("health", help="Check package and runtime health.")
    health_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
        help="Output format (default: text).",
    )
    claude_parser = subparsers.add_parser(
        "claude-config",
        help="Validate Claude key and role routes without making an API call.",
    )
    claude_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
        help="Output format (default: text).",
    )
    return parser


def _render_health(report: HealthReport, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(asdict(report), ensure_ascii=False, sort_keys=True)
    return (
        f"status={report.status} "
        f"package={report.package} "
        f"version={report.version} "
        f"python={report.python} "
        f"python_requirement={report.python_requirement}"
    )


def _render_claude_config(
    settings: AnthropicRuntimeSettings,
    output_format: str,
) -> str:
    role_models = {role.value: model_id for role, model_id in settings.role_model_ids.items()}
    if output_format == "json":
        return json.dumps(
            {
                "status": "ok",
                "api_key_configured": True,
                "timeout_seconds": settings.timeout_seconds,
                "max_retries": settings.max_retries,
                "role_models": role_models,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    routes = ",".join(f"{role}={model}" for role, model in sorted(role_models.items()))
    return (
        "status=ok api_key_configured=true "
        f"timeout_seconds={settings.timeout_seconds:g} "
        f"max_retries={settings.max_retries} role_models={routes}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = cast(str, args.command)

    if command == "health":
        report = build_health_report()
        output_format = cast(str, args.output_format)
        print(_render_health(report, output_format))
        return 0 if report.status == "ok" else 1

    if command == "claude-config":
        output_format = cast(str, args.output_format)
        try:
            settings = AnthropicRuntimeSettings.from_environment()
        except (ValidationError, ValueError):
            if output_format == "json":
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "reason": "invalid_or_missing_claude_configuration",
                        },
                        sort_keys=True,
                    )
                )
            else:
                print(
                    "status=error reason=invalid_or_missing_claude_configuration",
                    file=sys.stderr,
                )
            return 2
        print(_render_claude_config(settings, output_format))
        return 0

    parser.error(f"unknown command: {command}")
