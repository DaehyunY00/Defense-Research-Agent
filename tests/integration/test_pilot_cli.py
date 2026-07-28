"""Integration tests for reproducible offline pilot and evaluation CLIs."""

import json
import subprocess
import sys
from pathlib import Path

from defense_research_agent.domain import (
    IngestionReport,
    PublicationType,
    ResearchPublication,
)

PROJECT_ROOT = Path(__file__).parents[2]


def _prepare_inputs(tmp_path: Path) -> tuple[Path, Path]:
    normalized_path = tmp_path / "artifacts" / "normalized" / "publications.jsonl"
    normalized_path.parent.mkdir(parents=True)
    publication = ResearchPublication(
        publication_id="pub:pilot-cli",
        publication_type=PublicationType.DEFENSE_FORUM,
        title="국방 인공지능 정책 연구",
        raw_metadata={"_ingestion": {"filename_year": 2024}},
    )
    normalized_path.write_text(publication.model_dump_json() + "\n", encoding="utf-8")
    report_path = tmp_path / "artifacts" / "reports" / "ingestion_report.json"
    report_path.parent.mkdir(parents=True)
    report = IngestionReport(
        input_path="data",
        publications_path=str(normalized_path),
        total_file_count=1,
        success_count=1,
        failure_count=0,
        skipped_count=0,
        publication_count=1,
        publication_type_counts={"defense_forum": 1},
        suspected_duplicate_count=0,
        suspected_duplicate_group_count=0,
        missing_field_counts={
            "title": 0,
            "authors": 1,
            "publication_date": 1,
        },
    )
    report_path.write_text(report.model_dump_json(), encoding="utf-8")
    return normalized_path, report_path


def test_offline_pilot_is_reproducible_and_evaluation_cli_writes_outputs(
    tmp_path: Path,
) -> None:
    normalized_path, report_path = _prepare_inputs(tmp_path)
    artifacts_root = tmp_path / "artifacts"
    pilot_command = [
        sys.executable,
        "-m",
        "defense_research_agent.cli.run_offline_pilot",
        "--run-id",
        "integration-pilot",
        "--normalized",
        str(normalized_path),
        "--external-fixture",
        str(PROJECT_ROOT / "tests" / "fixtures" / "external_issues.json"),
        "--scoring-config",
        str(PROJECT_ROOT / "configs" / "scoring.json"),
        "--artifacts-root",
        str(artifacts_root),
        "--candidate-count",
        "1",
    ]

    first = subprocess.run(
        pilot_command,
        check=False,
        capture_output=True,
        text=True,
    )
    ranking_path = artifacts_root / "runs" / "integration-pilot" / "ranked_candidates.json"
    first_ranking = ranking_path.read_text(encoding="utf-8")
    second = subprocess.run(
        pilot_command,
        check=False,
        capture_output=True,
        text=True,
    )
    run_state = json.loads(
        (artifacts_root / "runs" / "integration-pilot" / "run_state.json").read_text(
            encoding="utf-8"
        )
    )

    assert first.returncode == second.returncode == 0
    assert ranking_path.read_text(encoding="utf-8") == first_ranking
    assert run_state["status"] == "awaiting_review"
    assert run_state["human_approved"] is False
    assert run_state["orchestration_audit"]["reproduction_match"] is True
    assert not (
        artifacts_root / "runs" / "integration-pilot" / "topic_planning_cards.json"
    ).exists()

    evaluation = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent.cli.evaluate_pilot",
            "--run-id",
            "integration-pilot",
            "--artifacts-root",
            str(artifacts_root),
            "--normalized",
            str(normalized_path),
            "--ingestion-report",
            str(report_path),
            "--external-fixture",
            str(PROJECT_ROOT / "tests" / "fixtures" / "external_issues.json"),
            "--cutoff-year",
            "2024",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert evaluation.returncode == 0
    assert (artifacts_root / "evaluation" / "evaluation_summary.json").is_file()
    assert (artifacts_root / "evaluation" / "evaluation_report.md").is_file()
    assert (artifacts_root / "evaluation" / "expert_review_template.csv").is_file()
