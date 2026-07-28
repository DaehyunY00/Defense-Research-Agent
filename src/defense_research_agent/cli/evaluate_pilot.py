"""CLI for reproducible offline pilot evaluation artifacts."""

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from defense_research_agent.domain.evaluation import CandidateEvaluation
from defense_research_agent.domain.ingestion import IngestionReport
from defense_research_agent.domain.pilot_evaluation import OrchestrationAudit
from defense_research_agent.evaluation.harness import (
    PilotEvaluationHarness,
    load_publications,
)
from defense_research_agent.issues import MockExternalIssueSearchProvider
from defense_research_agent.repositories import InMemoryResearchPublicationRepository
from defense_research_agent.services.external_issues import ExternalIssueNormalizationService
from defense_research_agent.services.review import load_ranked_topics

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m defense_research_agent.cli.evaluate_pilot",
        description="Evaluate one offline pilot run without external APIs.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifacts-root", type=Path, default=Path("artifacts"))
    parser.add_argument(
        "--normalized",
        type=Path,
        default=Path("artifacts/normalized/publications.jsonl"),
    )
    parser.add_argument(
        "--ingestion-report",
        type=Path,
        default=Path("artifacts/reports/ingestion_report.json"),
    )
    parser.add_argument(
        "--external-fixture",
        type=Path,
        default=Path("tests/fixtures/external_issues.json"),
    )
    parser.add_argument("--cutoff-year", type=int, default=2024)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load run artifacts, calculate metrics, and write the required three files."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    run_id = cast(str, args.run_id)
    artifacts_root = cast(Path, args.artifacts_root)
    normalized_path = cast(Path, args.normalized)
    report_path = cast(Path, args.ingestion_report)
    fixture_path = cast(Path, args.external_fixture)
    cutoff_year = cast(int, args.cutoff_year)
    if not _SAFE_RUN_ID.fullmatch(run_id):
        parser.error("--run-id must be a safe path segment")
    run_dir = artifacts_root / "runs" / run_id
    ranking_path = run_dir / "ranked_candidates.json"
    evaluation_path = run_dir / "evaluation_results.json"
    for required_path in (
        normalized_path,
        report_path,
        fixture_path,
        ranking_path,
        evaluation_path,
    ):
        if not required_path.is_file():
            parser.error(f"required input does not exist: {required_path}")

    try:
        publications = load_publications(normalized_path)
        repository = InMemoryResearchPublicationRepository(publications)
        ingestion_report = IngestionReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
        ranked_topics = load_ranked_topics(ranking_path)
        candidate_evaluations = _load_candidate_evaluations(evaluation_path)
        run_state_path = run_dir / "run_state.json"
        orchestration_audit = _load_orchestration_audit(run_state_path)
        provider = MockExternalIssueSearchProvider(fixture_path)
        search_result = provider.search_recent_issues_with_status(
            "",
            None,
            None,
            [],
            100,
        )
        normalized_issues = ExternalIssueNormalizationService().normalize_search_result(
            search_result
        )
        harness = PilotEvaluationHarness(repository, publications)
        summary = harness.evaluate(
            run_id=run_id,
            ingestion_report=ingestion_report,
            candidates=[topic.candidate for topic in ranked_topics],
            signals=normalized_issues.topic_signals,
            candidate_evaluations=candidate_evaluations,
            ranked_topics=ranked_topics,
            cutoff_year=cutoff_year,
            orchestration_audit=orchestration_audit,
        )
        paths = harness.write_outputs(
            artifacts_root / "evaluation",
            summary,
            ranked_topics,
        )
    except ValueError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                "run_id": run_id,
                "evaluation_summary": str(paths[0]),
                "evaluation_report": str(paths[1]),
                "expert_review_template": str(paths[2]),
                "failure_case_count": len(summary.failure_cases),
                "temporal_leakage_count": summary.temporal_backtest.leakage_count,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _load_candidate_evaluations(path: Path) -> list[CandidateEvaluation]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evaluation artifact must be a JSON object")
    raw_evaluations = payload.get("candidate_evaluations")
    if not isinstance(raw_evaluations, list):
        raise ValueError("evaluation artifact has no candidate_evaluations list")
    return [CandidateEvaluation.model_validate(evaluation) for evaluation in raw_evaluations]


def _load_orchestration_audit(path: Path) -> OrchestrationAudit | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("run state artifact must be a JSON object")
    raw_audit = payload.get("orchestration_audit")
    if raw_audit is None:
        return None
    return OrchestrationAudit.model_validate(raw_audit)


if __name__ == "__main__":
    raise SystemExit(main())
