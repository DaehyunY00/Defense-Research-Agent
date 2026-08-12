"""Prepare the two work packages that only a human can complete.

1. ``manual_review`` queue — the DQ-04 documents the quality gate holds out of the
   default index, with the evidence a reviewer needs to decide each one.
2. Golden retrieval dataset scaffolding — a stratified candidate pool, per-type
   coverage figures, and a fill-in template matching ``docs/EVALUATION.md``.

Read-only over ``data/``. Verifies a source-tree hash before and after, per
AGENTS.md rule 1. Everything is written under ``artifacts/human_review/``.

Run:  uv run python scripts/prepare_human_review.py
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any
from unicodedata import category

from defense_research_agent.data_integrity import corpus_digest
from defense_research_agent.domain import (
    MetadataField,
    PublicationQualityStatus,
    ResearchPublication,
)
from defense_research_agent.evaluation.quality import (
    DeterministicPublicationQualityGate,
)
from defense_research_agent.search.metadata import RuleBasedPublicationMetadataExtractor
from defense_research_agent.search.parsers import JsonPageParser
from defense_research_agent.services.ingestion import IngestionService

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = REPOSITORY_ROOT / "data"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "human_review"

DATASET_VERSION = "golden-retrieval-v1-draft"
TARGET_QUESTION_COUNT = 40
COVER_PREVIEW_CHARACTERS = 400

GOLDEN_COLUMNS = (
    "case_id",
    "question",
    "topic",
    "difficulty",
    "query_slice",
    "relevant_publication_ids",
    "relevant_pages",
    "relevance_grade",
    "expected_evidence",
    "acceptable_abstention",
    "created_by",
    "reviewed_by",
    "adjudication_status",
    "notes",
)


def source_tree_digest() -> str:
    """Content hash over the research corpus. See defense_research_agent.data_integrity."""
    return corpus_digest(DATA_DIRECTORY)


def _quality_gate_publication(publication: ResearchPublication) -> ResearchPublication:
    """Expose the original JSON filename to DQ-04, as the corpus builder does."""
    ingestion = publication.raw_metadata.get("_ingestion")
    source_filename = publication.raw_metadata.get("filename")
    if (
        not isinstance(ingestion, dict)
        or ingestion.get("title_source") != "filename"
        or not isinstance(source_filename, str)
        or not source_filename.strip()
    ):
        return publication
    return publication.model_copy(update={"local_path": source_filename})


def _publication_year(publication: ResearchPublication) -> str:
    filename = publication.raw_metadata.get("filename")
    if isinstance(filename, str) and len(filename) >= 4 and filename[:4].isdigit():
        return filename[:4]
    return "unknown"


def _sanitize(text: str) -> str:
    """Drop C0/C1 controls for display only. Stored page text is never rewritten.

    The corpus carries U+0001 as a space substitute (ADR-010). Leaving it in a
    CSV makes the file unreadable to spreadsheet software.
    """
    stripped = "".join(" " if category(ch) in {"Cc", "Cf"} else ch for ch in text)
    return " ".join(stripped.split())


def _cover_preview(pages: list[Any]) -> str:
    if not pages:
        return ""
    return _sanitize(pages[0].text)[:COVER_PREVIEW_CHARACTERS]


def _suggest_title(metadata: Any) -> tuple[str, str, str]:
    """Return (suggested_title, evidence_source, confidence) from the P1.5 extractor.

    This is a proposal for human verification, never an authoritative title.
    ``EVALUATION.md`` forbids using a machine label without review.
    """
    for value in metadata.values:
        if value.field is not MetadataField.TITLE:
            continue
        if value.normalized is None:
            return "", "", value.failure_reason or "제목 미확정"
        source = value.evidence.source.value if value.evidence else ""
        return _sanitize(value.normalized), source, f"{value.confidence:.2f}"
    return "", "", "제목 후보 없음"


def _write_excel_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a UTF-16LE tab-separated copy that macOS Excel opens correctly."""
    if not rows:
        return
    columns = list(rows[0])
    lines = ["\t".join(columns)]
    lines.extend(
        "\t".join(str(row[column]).replace("\t", " ") for column in columns) for row in rows
    )
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-16-le")


def main() -> None:
    before = source_tree_digest()
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    outcome = IngestionService().ingest(
        DATA_DIRECTORY,
        OUTPUT_DIRECTORY / "normalized",
        OUTPUT_DIRECTORY / "ingestion_report.json",
    )
    parser = JsonPageParser()
    gate = DeterministicPublicationQualityGate()
    extractor = RuleBasedPublicationMetadataExtractor()

    manual_rows: list[dict[str, Any]] = []
    indexable: list[tuple[ResearchPublication, int]] = []
    status_counts: Counter[str] = Counter()
    orphan_publication_ids: list[str] = []
    seen: dict[str, str] = {}

    for publication in sorted(outcome.publications, key=lambda p: p.publication_id):
        # The ingestion lineage records which document JSON was selected and its
        # checksum. Never re-derive this from file names — DQ-04 truncation and
        # DQ-07 normalization make name matching unreliable.
        lineage = publication.raw_metadata.get("_ingestion")
        selected = lineage.get("selected_source_path") if isinstance(lineage, dict) else None
        checksums = lineage.get("json_source_checksums") if isinstance(lineage, dict) else None
        if not isinstance(selected, str) or not isinstance(checksums, list) or not checksums:
            # A production-ingested orphan PDF deliberately has no JSON pages.
            orphan_publication_ids.append(publication.publication_id)
            status_counts["orphan_pdf"] += 1
            continue
        source = DATA_DIRECTORY / selected

        result = parser.parse(source, str(checksums[0]))
        judged = _quality_gate_publication(publication)
        verdict = gate.evaluate(judged, result.pages, seen)
        status_counts[verdict.status.value] += 1

        if verdict.status is PublicationQualityStatus.MANUAL_REVIEW:
            extracted = extractor.extract(publication, result.pages, source)
            suggested, evidence_source, confidence = _suggest_title(extracted)
            manual_rows.append(
                {
                    "publication_id": publication.publication_id,
                    "publication_type": publication.publication_type.value,
                    "source_filename": publication.raw_metadata.get("filename", ""),
                    "filename_bytes": len(
                        str(publication.raw_metadata.get("filename", "")).encode("utf-8")
                    ),
                    "review_reasons": " | ".join(verdict.reasons),
                    "page_count": verdict.measurements.page_count,
                    "character_count": verdict.measurements.character_count,
                    "suggested_title": suggested,
                    "suggestion_evidence": evidence_source,
                    "suggestion_confidence": confidence,
                    "cover_page_preview": _cover_preview(result.pages),
                    "decision": "",
                    "confirmed_title": "",
                    "reviewer_notes": "",
                }
            )
        elif verdict.status.is_indexable:
            body = "".join(page.text for page in result.pages)
            seen.setdefault(sha256(body.encode("utf-8")).hexdigest(), publication.publication_id)
            indexable.append((publication, verdict.measurements.character_count))

    # --- 1. manual review queue -------------------------------------------------
    queue_path = OUTPUT_DIRECTORY / "manual_review_queue.csv"
    with queue_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manual_rows[0])) if manual_rows else None
        if writer is not None:
            writer.writeheader()
            writer.writerows(manual_rows)

    # macOS Excel misreads UTF-8 CSV even with a BOM. UTF-16LE TSV opens
    # correctly there, so both forms are written and the reviewer picks one.
    _write_excel_tsv(OUTPUT_DIRECTORY / "manual_review_queue.tsv", manual_rows)

    # --- 2. golden dataset scaffolding -----------------------------------------
    by_stratum: dict[tuple[str, str], list[tuple[ResearchPublication, int]]] = defaultdict(list)
    for publication, characters in indexable:
        by_stratum[(publication.publication_type.value, _publication_year(publication))].append(
            (publication, characters)
        )

    # Proportional allocation, largest documents first inside each stratum so a
    # question writer sees substantive material rather than short notices.
    total = len(indexable)
    candidates: list[dict[str, Any]] = []
    for (publication_type, year), members in sorted(by_stratum.items()):
        quota = max(1, round(TARGET_QUESTION_COUNT * len(members) / total)) if total else 0
        ranked = sorted(members, key=lambda item: (-item[1], item[0].publication_id))
        for publication, characters in ranked[:quota]:
            candidates.append(
                {
                    "publication_id": publication.publication_id,
                    "publication_type": publication_type,
                    "year": year,
                    "title": publication.title or "",
                    "source_filename": publication.raw_metadata.get("filename", ""),
                    "character_count": characters,
                }
            )

    candidates_path = OUTPUT_DIRECTORY / "golden_candidate_pool.csv"
    with candidates_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(candidates[0]))
        writer.writeheader()
        writer.writerows(candidates)

    template_path = OUTPUT_DIRECTORY / "golden_questions_template.csv"
    with template_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(GOLDEN_COLUMNS)
        for index in range(1, TARGET_QUESTION_COUNT + 1):
            row = [""] * len(GOLDEN_COLUMNS)
            row[0] = f"gq-{index:03d}"
            row[GOLDEN_COLUMNS.index("adjudication_status")] = "draft"
            writer.writerow(row)

    coverage = {
        "dataset_version": DATASET_VERSION,
        "corpus_snapshot_digest": before,
        "target_question_count": TARGET_QUESTION_COUNT,
        "indexable_document_count": total,
        "manual_review_count": len(manual_rows),
        "quality_status_counts": dict(sorted(status_counts.items())),
        "orphan_without_document_json": orphan_publication_ids,
        "candidate_pool_size": len(candidates),
        "candidate_pool_by_type": dict(
            sorted(Counter(row["publication_type"] for row in candidates).items())
        ),
        "indexable_by_type": dict(
            sorted(Counter(p.publication_type.value for p, _ in indexable).items())
        ),
    }
    (OUTPUT_DIRECTORY / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    after = source_tree_digest()
    if before != after:
        raise SystemExit(f"data/ changed during the run: {before} -> {after}")

    print(json.dumps(coverage, ensure_ascii=False, indent=2))
    print(f"\nmanual review queue : {queue_path}")
    print(f"golden candidate pool: {candidates_path}")
    print(f"golden question form : {template_path}")
    print(f"\ndata/ immutable: {before[:16]}… unchanged")


if __name__ == "__main__":
    main()
