"""Map a prior research Q&A dataset onto this corpus as golden-dataset drafts.

The earlier KIDA RAG study authored 200 Q&A pairs over the same source corpus.
Their ``source_document`` field is a retrieval label, so the questions can seed
this project's golden dataset instead of starting from an empty template.

Two things this script does not do, on purpose:

- It does not produce final labels. Every row lands as ``adjudication_status
  =draft`` with the mapping evidence attached, because ``EVALUATION.md`` forbids
  using an unreviewed label as golden truth.
- It does not invent page numbers. The prior dataset labels documents, not
  pages, so ``relevant_pages`` is left empty for a reviewer to fill.

Read-only over ``data/`` and over the prior study directory. Output goes to
``artifacts/human_review/``.

Run:  uv run python scripts/import_prior_test_dataset.py [prior_repository_path]
"""

from __future__ import annotations

import ast
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from defense_research_agent.data_integrity import corpus_digest
from defense_research_agent.human_review import apply_review_decisions, load_review_decisions
from defense_research_agent.services.ingestion import IngestionService

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = REPOSITORY_ROOT / "data"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "human_review"
REVIEW_DECISIONS_PATH = OUTPUT_DIRECTORY / "manual_review_decisions.csv"
DEFAULT_PRIOR_ROOT = Path("/Users/daehyunyoo/Desktop/KIDA_RAG_System")
PRIOR_DATASET_RELATIVE = Path("src/evaluation/test_dataset.py")

# The prior study's categories were chosen to exercise specific retrieval
# mechanisms, which is exactly what a query slice is for.
CATEGORY_TO_SLICE = {
    "KEYWORD_MATCHING": "exact_term",
    "SEMANTIC_REASONING": "concept",
    "MULTI_HOP": "multi_hop",
    "TRAP_QUESTION": "abstention",
}

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
    "prior_case_id",
    "prior_source_document",
    "mapping_confidence",
    "prior_keywords",
)


def _normalize(text: str) -> str:
    """Fold to a comparable form: NFC, lowercase, alphanumerics and Hangul only."""
    composed = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"[^0-9a-z가-힣]", "", composed)


def _parse_prior_pairs(source: Path) -> list[dict[str, Any]]:
    """Read ``TestQAPair(...)`` literals without importing the prior package.

    The prior module imports its own package, which is not installed here, so the
    file is parsed as a syntax tree instead of executed.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    pairs: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "TestQAPair":
            continue
        record: dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            record[keyword.arg] = _literal(keyword.value)
        if record.get("id"):
            pairs.append(record)
    return pairs


def _literal(node: ast.expr) -> Any:
    """Evaluate a constant, list, or ``Enum.MEMBER`` attribute access."""
    if isinstance(node, ast.Attribute):
        return node.attr
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _publication_index() -> dict[str, tuple[str, str]]:
    """Map normalized file stems to (publication_id, original stem)."""
    outcome = IngestionService().ingest(
        DATA_DIRECTORY,
        OUTPUT_DIRECTORY / "normalized",
        OUTPUT_DIRECTORY / "ingestion_report.json",
    )
    approved: dict[str, str] = {}
    if REVIEW_DECISIONS_PATH.exists():
        approved = load_review_decisions(REVIEW_DECISIONS_PATH).approved_titles

    index: dict[str, tuple[str, str]] = {}
    for publication in outcome.publications:
        reviewed = apply_review_decisions(publication, approved)
        if reviewed.local_path is None:
            continue
        stem = Path(reviewed.local_path).stem
        index[_normalize(stem)] = (reviewed.publication_id, stem)
    return index


def _match(source_document: str, index: dict[str, tuple[str, str]]) -> tuple[str, str, str]:
    """Return (publication_id, matched_stem, confidence) for a prior label.

    ``exact`` means the normalized stems are identical. ``prefix`` means one is a
    prefix of the other, which happens because the prior study stored truncated
    file names. ``none`` means the label named a topic rather than a document,
    such as "3축 체계 관련 문서"; those must be resolved by a reviewer.
    """
    needle = _normalize(source_document)
    if not needle:
        return "", "", "none"
    if needle in index:
        publication_id, stem = index[needle]
        return publication_id, stem, "exact"

    candidates = [
        (key, value)
        for key, value in index.items()
        if key.startswith(needle) or needle.startswith(key)
    ]
    if len(candidates) == 1:
        publication_id, stem = candidates[0][1]
        return publication_id, stem, "prefix"
    if len(candidates) > 1:
        return "", "", "ambiguous"
    return "", "", "none"


def main() -> None:
    prior_root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PRIOR_ROOT
    dataset_path = prior_root / PRIOR_DATASET_RELATIVE
    if not dataset_path.exists():
        raise SystemExit(f"prior dataset not found: {dataset_path}")

    before = corpus_digest(DATA_DIRECTORY)
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    pairs = _parse_prior_pairs(dataset_path)
    index = _publication_index()

    rows: list[dict[str, str]] = []
    confidence_counts: Counter[str] = Counter()
    slice_counts: Counter[str] = Counter()

    for ordinal, pair in enumerate(pairs, start=1):
        category = str(pair.get("category") or "")
        query_slice = CATEGORY_TO_SLICE.get(category, "")
        source_document = str(pair.get("source_document") or "")
        publication_id, matched_stem, confidence = _match(source_document, index)
        negative = bool(pair.get("negative_test"))

        confidence_counts[confidence] += 1
        slice_counts[query_slice or "(unmapped)"] += 1

        notes = []
        if pair.get("notes"):
            notes.append(str(pair["notes"]))
        if matched_stem:
            notes.append(f"매핑: {matched_stem}")
        if confidence in {"none", "ambiguous"}:
            notes.append("문서 라벨을 특정할 수 없음. 검토자가 확인 필요")
        if pair.get("multi_doc_required"):
            notes.append("다중 문서 근거 필요")

        rows.append(
            {
                "case_id": f"gq-{ordinal:03d}",
                "question": str(pair.get("question") or ""),
                "topic": str(pair.get("domain") or ""),
                "difficulty": str(pair.get("difficulty") or "").lower(),
                "query_slice": query_slice,
                "relevant_publication_ids": publication_id,
                "relevant_pages": "",
                "relevance_grade": "3" if publication_id and not negative else "",
                "expected_evidence": str(pair.get("expected_answer") or ""),
                "acceptable_abstention": "yes" if negative else "no",
                "created_by": "",
                "reviewed_by": "",
                "adjudication_status": "draft",
                "notes": " | ".join(notes),
                "prior_case_id": str(pair.get("id") or ""),
                "prior_source_document": source_document,
                "mapping_confidence": confidence,
                "prior_keywords": ";".join(pair.get("keywords") or []),
            }
        )

    drafts_path = OUTPUT_DIRECTORY / "golden_questions_drafts.csv"
    with drafts_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(GOLDEN_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    resolved = sum(1 for row in rows if row["relevant_publication_ids"])
    report = {
        "prior_dataset": str(dataset_path),
        "prior_pair_count": len(pairs),
        "corpus_snapshot_digest": before,
        "indexed_publication_count": len(index),
        "rows_with_resolved_publication": resolved,
        "rows_needing_manual_resolution": len(rows) - resolved,
        "mapping_confidence": dict(sorted(confidence_counts.items())),
        "query_slice_counts": dict(sorted(slice_counts.items())),
        "abstention_rows": sum(1 for row in rows if row["acceptable_abstention"] == "yes"),
        "unresolved_source_documents": sorted(
            {
                row["prior_source_document"]
                for row in rows
                if not row["relevant_publication_ids"] and row["prior_source_document"]
            }
        ),
    }
    (OUTPUT_DIRECTORY / "prior_dataset_import_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    after = corpus_digest(DATA_DIRECTORY)
    if before != after:
        raise SystemExit(f"data/ changed during the run: {before} -> {after}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n초안: {drafts_path}")
    print(f"data/ immutable: {before[:16]}… unchanged")


if __name__ == "__main__":
    main()
