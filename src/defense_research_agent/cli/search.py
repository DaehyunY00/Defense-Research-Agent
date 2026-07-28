"""CLI for deterministic local search over normalized publications."""

import argparse
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import cast

from defense_research_agent.domain import (
    PublicationSearchFilters,
    PublicationSearchResult,
    PublicationType,
)
from defense_research_agent.repositories import InMemoryResearchPublicationRepository

_DEFAULT_INDEX_PATH = Path("artifacts/normalized/publications.jsonl")


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO date in YYYY-MM-DD form") from error


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m defense_research_agent.cli.search",
        description="Search normalized KIDA publications without an external database.",
    )
    parser.add_argument("--query", required=True, help="Korean or English search query.")
    parser.add_argument(
        "--index",
        type=Path,
        default=_DEFAULT_INDEX_PATH,
        help=f"Normalized JSONL path (default: {_DEFAULT_INDEX_PATH}).",
    )
    parser.add_argument(
        "--type",
        choices=tuple(publication_type.value for publication_type in PublicationType),
        action="append",
        dest="publication_types",
        help="Publication type filter; repeat to allow multiple types.",
    )
    parser.add_argument(
        "--author",
        action="append",
        dest="authors",
        help="Author filter; repeat to allow multiple authors.",
    )
    parser.add_argument("--start-date", type=_iso_date, default=None)
    parser.add_argument("--end-date", type=_iso_date, default=None)
    parser.add_argument("--limit", type=int, default=10)
    return parser


def _result_payload(result: PublicationSearchResult) -> dict[str, object]:
    publication = result.publication
    return {
        "publication_id": publication.publication_id,
        "publication_type": publication.publication_type.value,
        "title": publication.title,
        "authors": publication.authors,
        "publication_date": (
            publication.publication_date.isoformat()
            if publication.publication_date is not None
            else None
        ),
        "local_path": publication.local_path,
        "score": result.score,
        "matched_fields": [field.value for field in result.matched_fields],
        "matched_terms": result.matched_terms,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Load the local JSONL index, search it, and print compact JSON."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    query = cast(str, args.query)
    index_path = cast(Path, args.index)
    limit = cast(int, args.limit)
    publication_type_values = cast(list[str] | None, args.publication_types)
    authors = cast(list[str] | None, args.authors)
    start_date = cast(date | None, args.start_date)
    end_date = cast(date | None, args.end_date)

    if limit < 0:
        parser.error("--limit must be zero or greater")
    if not index_path.is_file():
        parser.error(f"normalized index does not exist: {index_path}")

    try:
        filters = PublicationSearchFilters(
            start_date=start_date,
            end_date=end_date,
            publication_types=[PublicationType(value) for value in publication_type_values or ()],
            authors=authors or [],
        )
        repository = InMemoryResearchPublicationRepository.from_jsonl(index_path)
    except ValueError as error:
        parser.error(str(error))

    results = repository.search(query, filters, limit)
    payload = {
        "query": query,
        "result_count": len(results),
        "results": [_result_payload(result) for result in results],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
