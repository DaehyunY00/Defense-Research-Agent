"""Validate and approve one deployment corpus index."""

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from defense_research_agent.services.corpus_index import (
    build_corpus_index_manifest,
    corpus_manifest_object_name,
    write_corpus_manifest,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m defense_research_agent.cli.corpus_index",
        description="Validate a public ResearchPublication JSONL and record human approval.",
    )
    parser.add_argument("--index", type=Path, required=True, dest="index_path")
    parser.add_argument("--output", type=Path, required=True, dest="output_path")
    parser.add_argument("--reviewed-by", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Write a content-bound manifest and print upload object names."""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    index_path = cast(Path, args.index_path).resolve()
    output_path = cast(Path, args.output_path).resolve()
    reviewed_by = cast(str, args.reviewed_by)
    if output_path == index_path:
        raise ValueError("manifest output must not overwrite the publication index")
    manifest = build_corpus_index_manifest(
        index_path.read_bytes(),
        reviewed_by=reviewed_by,
        reviewed_at=datetime.now(UTC),
    )
    write_corpus_manifest(output_path, manifest)
    print(
        json.dumps(
            {
                "index_object": manifest.index_object,
                "index_sha256": manifest.index_sha256,
                "index_size_bytes": manifest.index_size_bytes,
                "manifest_object": corpus_manifest_object_name(manifest),
                "publication_count": manifest.publication_count,
                "reviewed_by": manifest.reviewed_by,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
