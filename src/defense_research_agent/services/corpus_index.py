"""Build a human-reviewed manifest for one normalized publication index."""

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from defense_research_agent.domain import CorpusIndexManifest
from defense_research_agent.repositories.publication_index import (
    DEFAULT_MAX_CORPUS_BYTES,
    DEFAULT_MAX_CORPUS_PUBLICATIONS,
    parse_publication_index,
)


def build_corpus_index_manifest(
    payload: bytes,
    *,
    reviewed_by: str,
    reviewed_at: datetime,
    max_bytes: int = DEFAULT_MAX_CORPUS_BYTES,
    max_publications: int = DEFAULT_MAX_CORPUS_PUBLICATIONS,
) -> CorpusIndexManifest:
    """Validate an index and bind its digest to one explicit reviewer."""
    publications = parse_publication_index(
        payload,
        max_bytes=max_bytes,
        max_publications=max_publications,
    )
    digest = sha256(payload).hexdigest()
    return CorpusIndexManifest(
        index_object=f"corpus/indexes/publications-{digest}.jsonl",
        index_sha256=digest,
        index_size_bytes=len(payload),
        publication_count=len(publications),
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )


def corpus_manifest_object_name(manifest: CorpusIndexManifest) -> str:
    """Return a content-addressed manifest name coupled to its index."""
    return f"corpus/manifests/{manifest.index_sha256}.json"


def write_corpus_manifest(path: Path, manifest: CorpusIndexManifest) -> None:
    """Atomically write deterministic UTF-8 JSON outside the source corpus."""
    content = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8", newline="\n")
    temporary_path.replace(path)
