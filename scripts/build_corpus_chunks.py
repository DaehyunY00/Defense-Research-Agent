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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from defense_research_agent.data_integrity import corpus_digest
from defense_research_agent.domain import (
    PublicationPage,
    PublicationQualityStatus,
    PublicationQualityVerdict,
    ResearchPublication,
)
from defense_research_agent.evaluation.quality import (
    DeterministicPublicationQualityGate,
    PublicationQualityArtifactWriter,
    select_default_index_publications,
)
from defense_research_agent.human_review import (
    apply_review_decisions,
    load_review_decisions,
)
from defense_research_agent.search.chunking import (
    CHUNK_MANIFEST_FILENAME,
    CHUNKS_FILENAME,
    ChunkingDocument,
    write_chunk_artifacts,
)
from defense_research_agent.search.parsers import JsonPageParser, ParserErrorCode
from defense_research_agent.services.ingestion import IngestionOutcome, IngestionService

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = REPOSITORY_ROOT / "data"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "corpus"
REVIEW_DECISIONS_FILENAME = "manual_review_decisions.csv"
"""Reviewer-authored decisions. No script writes this file."""


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """One normalized publication and its parser audit observations."""

    publication: ResearchPublication
    pages: tuple[PublicationPage, ...]
    dropped_empty_page_count: int


def source_tree_digest() -> str:
    """Content hash over the research corpus. See defense_research_agent.data_integrity."""
    return corpus_digest(DATA_DIRECTORY)


def ingest_publications() -> IngestionOutcome:
    """Normalize the full corpus through the production ingestion boundary."""
    outcome = IngestionService().ingest(
        DATA_DIRECTORY,
        OUTPUT_DIRECTORY / "normalized",
        OUTPUT_DIRECTORY / "ingestion_report.json",
    )
    if outcome.report.failure_count:
        details = ", ".join(
            f"{failure.path}:{failure.error_type}" for failure in outcome.report.failures
        )
        raise RuntimeError(f"ingestion reported source failures: {details}")
    return outcome


def load_parsed_documents(
    publications: Sequence[ResearchPublication],
) -> list[ParsedDocument]:
    """Parse ingested JSON lineage and fail closed on unexpected parser losses."""
    parser = JsonPageParser()
    documents: list[ParsedDocument] = []

    for publication in publications:
        selected_json_source = _selected_json_source(publication)
        if selected_json_source is None:
            # A production-ingested orphan PDF deliberately has no JSON pages.
            # The quality gate excludes it before content-based branches run.
            documents.append(ParsedDocument(publication, (), 0))
            continue

        source_path, source_checksum = selected_json_source
        parse_result = parser.parse(source_path, source_checksum)
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
            _quality_gate_publication(publication),
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
    ingestion = ingest_publications()

    # A reviewer-confirmed cover title promotes a DQ-04 publication out of
    # manual_review. Absent the decision file nothing is promoted, so the
    # default remains the conservative one.
    # Derived from REPOSITORY_ROOT rather than a module constant so a test that
    # relocates the repository root is not affected by the real review file.
    decisions_path = REPOSITORY_ROOT / "artifacts" / "human_review" / REVIEW_DECISIONS_FILENAME
    approved_titles: dict[str, str] = {}
    if decisions_path.exists():
        approved_titles = load_review_decisions(decisions_path).approved_titles
    reviewed = [
        apply_review_decisions(publication, approved_titles)
        for publication in ingestion.publications
    ]

    documents = load_parsed_documents(reviewed)
    gate, verdicts = evaluate_quality(documents)
    quality_paths = PublicationQualityArtifactWriter(OUTPUT_DIRECTORY / "quality").write(
        list(verdicts.values()),
        gate.thresholds,
    )
    selected_documents = select_indexable_documents(documents, verdicts)

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
            "excluded_document_count": len(documents) - manifest.input_document_count,
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
            "quality_failure_report": quality_paths.failure_report.relative_to(
                REPOSITORY_ROOT
            ).as_posix(),
            "quality_remediation_queue": quality_paths.reextract_ocr_queue.relative_to(
                REPOSITORY_ROOT
            ).as_posix(),
        },
        "data_immutable": {
            "before_sha256": before_digest,
            "after_sha256": after_digest,
            "unchanged": True,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def select_indexable_documents(
    documents: Sequence[ParsedDocument],
    verdicts: Mapping[str, PublicationQualityVerdict],
) -> list[ParsedDocument]:
    """Apply the production admission boundary without losing parser observations."""
    documents_by_id = {document.publication.publication_id: document for document in documents}
    if len(documents_by_id) != len(documents):
        raise ValueError("parsed documents require unique publication_id values")

    selected_publications = select_default_index_publications(
        [document.publication for document in documents],
        verdicts,
    )
    return [documents_by_id[publication.publication_id] for publication in selected_publications]


def _selected_json_source(publication: ResearchPublication) -> tuple[Path, str] | None:
    """Resolve the production-ingested JSON source and checksum, failing closed."""
    ingestion = publication.raw_metadata.get("_ingestion")
    if not isinstance(ingestion, Mapping):
        raise RuntimeError(f"publication {publication.publication_id} has no ingestion lineage")

    raw_paths = ingestion.get("json_source_paths")
    raw_checksums = ingestion.get("json_source_checksums")
    if not isinstance(raw_paths, list) or not isinstance(raw_checksums, list):
        raise RuntimeError(
            f"publication {publication.publication_id} has invalid JSON source lineage"
        )
    if len(raw_paths) != len(raw_checksums):
        raise RuntimeError(
            f"publication {publication.publication_id} has mismatched JSON source lineage"
        )
    if not raw_paths:
        return None
    if not all(isinstance(path, str) for path in raw_paths) or not all(
        isinstance(checksum, str) for checksum in raw_checksums
    ):
        raise RuntimeError(
            f"publication {publication.publication_id} has non-string JSON source lineage"
        )
    json_source_paths = [path for path in raw_paths if isinstance(path, str)]
    json_source_checksums = [checksum for checksum in raw_checksums if isinstance(checksum, str)]

    selected_path = ingestion.get("selected_source_path")
    if not isinstance(selected_path, str) or selected_path not in json_source_paths:
        raise RuntimeError(f"publication {publication.publication_id} has no selected JSON source")
    selected_index = json_source_paths.index(selected_path)
    source_path = (DATA_DIRECTORY / selected_path).resolve()
    data_root = DATA_DIRECTORY.resolve()
    if not source_path.is_relative_to(data_root):
        raise RuntimeError(f"publication {publication.publication_id} JSON source escapes data/")
    return source_path, json_source_checksums[selected_index]


def _quality_gate_publication(publication: ResearchPublication) -> ResearchPublication:
    """Expose the original source filename to DQ-04 without changing canonical identity.

    The filesystem's linked PDF name may already be NFC-normalized or truncated,
    while the selected JSON metadata preserves the original NFD filename and its
    byte length. The production quality gate deliberately evaluates that original
    filename when the ingestion lineage says the title came from it.
    """
    ingestion = publication.raw_metadata.get("_ingestion")
    source_filename = publication.raw_metadata.get("filename")
    if (
        not isinstance(ingestion, Mapping)
        or ingestion.get("title_source") != "filename"
        or not isinstance(source_filename, str)
        or not source_filename.strip()
    ):
        return publication
    return publication.model_copy(update={"local_path": source_filename})


def _content_checksum(pages: Sequence[PublicationPage]) -> str:
    return sha256("".join(page.text for page in pages).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
