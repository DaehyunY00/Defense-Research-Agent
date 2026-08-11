"""Build deterministic page-aware chunk artifacts from the read-only corpus.

The source tree is hashed before and after the complete run. Document JSON is
parsed through ``JsonPageParser`` and admitted only when the production quality
gate returns ``ready`` or ``warning``. Generated files are confined to
``artifacts/corpus/``.

Run:  uv run python scripts/build_corpus_chunks.py
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from defense_research_agent.data.readers import (
    JsonPublicationReader,
    PublicationSource,
    SkipSourceFile,
)
from defense_research_agent.domain import (
    PublicationPage,
    PublicationQualityStatus,
    PublicationQualityVerdict,
    ResearchPublication,
)
from defense_research_agent.evaluation.quality import (
    DeterministicPublicationQualityGate,
    select_default_index_publications,
)
from defense_research_agent.search.chunking import (
    CHUNK_MANIFEST_FILENAME,
    CHUNKS_FILENAME,
    ChunkingDocument,
    write_chunk_artifacts,
)
from defense_research_agent.search.parsers import JsonPageParser, ParserErrorCode
from defense_research_agent.services.publication_type import classify_publication_type

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = REPOSITORY_ROOT / "data"
METADATA_DIRECTORY = DATA_DIRECTORY / "metadata"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "corpus"


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """One normalized publication and its parser audit observations."""

    publication: ResearchPublication
    pages: tuple[PublicationPage, ...]
    dropped_empty_page_count: int


def source_tree_digest() -> str:
    """Return a content-and-relative-path digest over every file in ``data/``."""
    digest = sha256()
    for path in sorted(DATA_DIRECTORY.rglob("*")):
        if not path.is_file():
            continue
        digest.update(path.relative_to(DATA_DIRECTORY).as_posix().encode("utf-8"))
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def load_parsed_documents() -> list[ParsedDocument]:
    """Read document JSON and fail closed on parser losses other than blank pages."""
    reader = JsonPublicationReader()
    parser = JsonPageParser()
    documents: list[ParsedDocument] = []

    for source_path in sorted(METADATA_DIRECTORY.glob("*.json")):
        try:
            source = reader.read(source_path, METADATA_DIRECTORY)
        except SkipSourceFile:
            continue

        publication = _publication_from_source(source_path, source)
        parse_result = parser.parse(source_path, source.checksum)
        unexpected_failures = [
            failure
            for failure in parse_result.failures
            if failure.code is not ParserErrorCode.EMPTY_PAGE
        ]
        if unexpected_failures:
            details = ", ".join(
                f"{failure.code.value}@{failure.page_number or 'document'}"
                for failure in unexpected_failures
            )
            raise RuntimeError(f"unexpected parser failure for {source_path.name}: {details}")

        documents.append(
            ParsedDocument(
                publication=publication,
                pages=tuple(parse_result.pages),
                dropped_empty_page_count=sum(
                    failure.code is ParserErrorCode.EMPTY_PAGE for failure in parse_result.failures
                ),
            )
        )

    return documents


def evaluate_quality(
    documents: Sequence[ParsedDocument],
) -> tuple[
    DeterministicPublicationQualityGate,
    dict[str, PublicationQualityVerdict],
]:
    """Evaluate in source order and register duplicate owners only after admission."""
    gate = DeterministicPublicationQualityGate()
    verdicts: dict[str, PublicationQualityVerdict] = {}
    admitted_content_checksums: dict[str, str] = {}

    for document in documents:
        publication = document.publication
        verdict = gate.evaluate(
            publication,
            document.pages,
            admitted_content_checksums,
        )
        verdicts[publication.publication_id] = verdict
        if verdict.status.is_indexable:
            admitted_content_checksums.setdefault(
                _content_checksum(document.pages),
                publication.publication_id,
            )

    return gate, verdicts


def main() -> None:
    """Build the corpus twice-reproducible artifact and print its audit summary."""
    before_digest = source_tree_digest()
    documents = load_parsed_documents()
    gate, verdicts = evaluate_quality(documents)
    publications = [document.publication for document in documents]
    selected_publications = select_default_index_publications(publications, verdicts)
    documents_by_id = {document.publication.publication_id: document for document in documents}
    selected_documents = [
        documents_by_id[publication.publication_id] for publication in selected_publications
    ]

    manifest = write_chunk_artifacts(
        [
            ChunkingDocument(
                document.publication,
                document.pages,
                dropped_empty_page_count=document.dropped_empty_page_count,
            )
            for document in selected_documents
        ],
        OUTPUT_DIRECTORY,
    )

    after_digest = source_tree_digest()
    if before_digest != after_digest:
        raise SystemExit(f"data/ changed during the run: {before_digest} -> {after_digest}")

    status_counts = Counter(verdict.status.value for verdict in verdicts.values())
    summary = {
        "selection": {
            "admitted_statuses": [
                status.value for status in PublicationQualityStatus if status.is_indexable
            ],
            "quality_thresholds": gate.thresholds.model_dump(mode="json"),
        },
        "results": {
            "source_document_count": len(documents),
            "quality_status_counts": {
                status.value: status_counts[status.value] for status in PublicationQualityStatus
            },
            "selected_document_count": manifest.input_document_count,
            "selected_parser_page_count": manifest.input_page_count,
            "selected_dropped_empty_page_count": manifest.dropped_empty_page_count,
            "chunk_count": manifest.chunk_count,
            "boundary_firing_counts": manifest.boundary_firing_counts.model_dump(mode="json"),
            "chunks_sha256": manifest.chunks_sha256,
        },
        "outputs": {
            "chunks": (OUTPUT_DIRECTORY / CHUNKS_FILENAME).relative_to(REPOSITORY_ROOT).as_posix(),
            "manifest": (OUTPUT_DIRECTORY / CHUNK_MANIFEST_FILENAME)
            .relative_to(REPOSITORY_ROOT)
            .as_posix(),
        },
        "data_immutable": {
            "before_sha256": before_digest,
            "after_sha256": after_digest,
            "unchanged": True,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def _publication_from_source(
    source_path: Path,
    source: PublicationSource,
) -> ResearchPublication:
    """Reproduce the established filename-keyed identity of the raw JSON corpus."""
    publication_id = f"pub:kida:{sha256(source_path.name.encode('utf-8')).hexdigest()[:32]}"
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=classify_publication_type(
            source_path,
            source.raw_metadata,
            source.content,
        ),
        title=Path(source.target_filename).stem,
        local_path=source_path.relative_to(REPOSITORY_ROOT).as_posix(),
        raw_metadata=source.raw_metadata,
        content=source.content,
        created_at=source.created_at,
        checksum=source.checksum,
    )


def _content_checksum(pages: Sequence[PublicationPage]) -> str:
    return sha256("".join(page.text for page in pages).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
