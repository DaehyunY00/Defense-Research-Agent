"""CLI for read-only ingestion of local KIDA publication files."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from defense_research_agent.services.ingestion import IngestionService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m defense_research_agent.cli.ingest",
        description="Normalize read-only KIDA source files into ResearchPublication JSONL.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        dest="input_dir",
        help="Read-only source directory to scan recursively.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        dest="output_dir",
        help="Directory for publications.jsonl.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        dest="report_path",
        help="Optional report path (default: <output parent>/reports/ingestion_report.json).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run ingestion and return non-zero when individual files failed."""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    input_dir = cast(Path, args.input_dir)
    output_dir = cast(Path, args.output_dir)
    report_path = cast(Path | None, args.report_path)

    outcome = IngestionService().ingest(input_dir, output_dir, report_path)
    print(
        json.dumps(
            outcome.report.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if outcome.report.failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
