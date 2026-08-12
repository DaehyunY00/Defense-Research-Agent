"""Propose page candidates for confidently mapped golden-dataset drafts.

Each draft names a publication but not a page. This script finds pages whose
text contains the question's keywords and presents them with enough surrounding
text for a reviewer to judge.

## A methodological warning that belongs in the output, not just here

The proposal is produced by keyword matching, which is what lexical search
does. If a reviewer confirms these pages without reading them, the golden pages
become "the pages lexical search finds", and the benchmark will then report
inflated Recall for lexical retrieval. The measurement would be circular.

Two things keep that from happening, and both are required:

- The reviewer reads ``page_evidence`` and decides from the content.
- Pages that answer the question but contain none of the keywords must be added
  by the reviewer. The proposal cannot surface them by construction.

``suggested_pages`` is therefore a navigation aid. ``relevant_pages`` is the
label, and only a person writes it.

Read-only over ``data/``. Output goes to ``artifacts/human_review/``.

Run:  uv run python scripts/propose_golden_pages.py
"""

from __future__ import annotations

import csv
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from defense_research_agent.data_integrity import corpus_digest
from defense_research_agent.human_review import apply_review_decisions, load_review_decisions
from defense_research_agent.search.parsers import JsonPageParser
from defense_research_agent.services.ingestion import IngestionService

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = REPOSITORY_ROOT / "data"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "human_review"
DRAFTS_PATH = OUTPUT_DIRECTORY / "golden_questions_drafts.csv"
REVIEW_DECISIONS_PATH = OUTPUT_DIRECTORY / "manual_review_decisions.csv"

MAX_SUGGESTED_PAGES = 4
EVIDENCE_WINDOW = 110
ACCEPTED_CONFIDENCE = {"exact"}

OUTPUT_COLUMNS = (
    "case_id",
    "question",
    "query_slice",
    "difficulty",
    "relevant_publication_ids",
    "document_title",
    "prior_keywords",
    "suggested_pages",
    "matched_keywords",
    "page_evidence",
    "relevant_pages",
    "relevance_grade",
    "reviewed_by",
    "adjudication_status",
    "notes",
)


def _sanitize(text: str) -> str:
    """Drop C0/C1 controls for display. Stored page text is never rewritten."""
    stripped = "".join(" " if unicodedata.category(ch) in {"Cc", "Cf"} else ch for ch in text)
    return " ".join(stripped.split())


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _pages_by_publication() -> dict[str, tuple[list[Any], str]]:
    """Return page lists and titles keyed by publication id."""
    outcome = IngestionService().ingest(
        DATA_DIRECTORY,
        OUTPUT_DIRECTORY / "normalized",
        OUTPUT_DIRECTORY / "ingestion_report.json",
    )
    approved: dict[str, str] = {}
    if REVIEW_DECISIONS_PATH.exists():
        approved = load_review_decisions(REVIEW_DECISIONS_PATH).approved_titles

    parser = JsonPageParser()
    result: dict[str, tuple[list[Any], str]] = {}
    for publication in outcome.publications:
        reviewed = apply_review_decisions(publication, approved)
        lineage = reviewed.raw_metadata.get("_ingestion")
        if not isinstance(lineage, dict):
            continue
        selected = lineage.get("selected_source_path")
        checksums = lineage.get("json_source_checksums")
        if not isinstance(selected, str) or not isinstance(checksums, list) or not checksums:
            continue
        parsed = parser.parse(DATA_DIRECTORY / selected, str(checksums[0]))
        result[reviewed.publication_id] = (list(parsed.pages), reviewed.title or "")
    return result


def _propose(keywords: list[str], pages: list[Any]) -> tuple[list[int], list[str], list[str]]:
    """Rank pages by how many distinct keywords they contain."""
    folded = [(kw, _fold(kw)) for kw in keywords if kw.strip()]
    scored: list[tuple[int, int, int, list[str]]] = []
    for page in pages:
        text = _fold(_sanitize(page.text))
        hits = [original for original, needle in folded if needle and needle in text]
        if hits:
            scored.append((-len(hits), -len(text), page.page_number, hits))
    scored.sort()

    numbers: list[int] = []
    matched: list[str] = []
    evidence: list[str] = []
    page_by_number = {page.page_number: page for page in pages}
    for _, _, number, hits in scored[:MAX_SUGGESTED_PAGES]:
        numbers.append(number)
        matched.append(",".join(hits))
        text = _sanitize(page_by_number[number].text)
        position = min((text.find(hit) for hit in hits if text.find(hit) >= 0), default=0)
        start = max(0, position - EVIDENCE_WINDOW // 2)
        evidence.append(f"p{number}: …{text[start : start + EVIDENCE_WINDOW]}…")
    return numbers, matched, evidence


def main() -> None:
    if not DRAFTS_PATH.exists():
        raise SystemExit(
            f"{DRAFTS_PATH.name} not found. Run scripts/import_prior_test_dataset.py first."
        )

    before = corpus_digest(DATA_DIRECTORY)
    drafts = list(csv.DictReader(DRAFTS_PATH.open(encoding="utf-8-sig")))
    selected = [
        row
        for row in drafts
        if row["mapping_confidence"] in ACCEPTED_CONFIDENCE
        and row["relevant_publication_ids"]
        and row["acceptable_abstention"] != "yes"
    ]
    documents = _pages_by_publication()

    rows: list[dict[str, str]] = []
    without_suggestion = 0
    slice_counts: Counter[str] = Counter()

    for row in selected:
        publication_id = row["relevant_publication_ids"]
        pages, title = documents.get(publication_id, ([], ""))
        keywords = [kw for kw in row["prior_keywords"].split(";") if kw]
        numbers, matched, evidence = _propose(keywords, pages)
        if not numbers:
            without_suggestion += 1
        slice_counts[row["query_slice"]] += 1

        rows.append(
            {
                "case_id": row["case_id"],
                "question": row["question"],
                "query_slice": row["query_slice"],
                "difficulty": row["difficulty"],
                "relevant_publication_ids": publication_id,
                "document_title": title,
                "prior_keywords": row["prior_keywords"],
                "suggested_pages": ",".join(str(number) for number in numbers),
                "matched_keywords": " | ".join(matched),
                "page_evidence": "  ||  ".join(evidence),
                "relevant_pages": "",
                "relevance_grade": "",
                "reviewed_by": "",
                "adjudication_status": "draft",
                "notes": row["notes"],
            }
        )

    output_path = OUTPUT_DIRECTORY / "golden_page_review.csv"
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "corpus_snapshot_digest": before,
        "draft_rows": len(drafts),
        "selected_rows": len(rows),
        "selection_rule": "mapping_confidence=exact, publication resolved, not an abstention case",
        "rows_without_page_suggestion": without_suggestion,
        "query_slice_counts": dict(sorted(slice_counts.items())),
        "methodology_warning": (
            "suggested_pages is keyword-derived and therefore biased toward what "
            "lexical retrieval finds. Confirming without reading page_evidence "
            "would inflate lexical Recall in the benchmark. Pages that answer the "
            "question without containing the keywords must be added manually."
        ),
    }
    (OUTPUT_DIRECTORY / "golden_page_review_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    after = corpus_digest(DATA_DIRECTORY)
    if before != after:
        raise SystemExit(f"data/ changed during the run: {before} -> {after}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n검토 파일: {output_path}")
    print(f"data/ immutable: {before[:16]}… unchanged")


if __name__ == "__main__":
    main()
