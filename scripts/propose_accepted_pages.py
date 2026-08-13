"""Propose evidence pages for accepted, non-abstention golden questions.

Scope: rows whose question was accepted in ``golden_question_decisions.csv`` and
which need a page label. Abstention rows are excluded because absence of an
answer needs no page.

## Why this scores against the answer, not the question

An earlier proposal ranked pages by the question's keywords. That is the signal
lexical retrieval uses at query time, so confirming those pages would have made
the benchmark measure itself: lexical Recall would rise simply because the
labels were drawn from lexical matches.

This script scores pages against the prior study's ``expected_answer`` instead.
The answer text is not the query, so it is not the signal any retriever sees.
The bias is smaller, though not zero — both are still lexical overlap.

## What this does not do

It does not write ``relevant_pages``. That column is the label, and
``EVALUATION.md`` rule 5 forbids using a model-produced label without review.
The proposal lands in ``proposed_pages`` with the page text attached so a
reviewer can confirm or correct it quickly.

Read-only over ``data/``. Output goes to ``artifacts/human_review/``.

Run:  uv run python scripts/propose_accepted_pages.py
"""

from __future__ import annotations

import csv
import json
import re
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
QUESTION_DECISIONS_PATH = OUTPUT_DIRECTORY / "golden_question_decisions.csv"
REVIEW_DECISIONS_PATH = OUTPUT_DIRECTORY / "manual_review_decisions.csv"

MAX_PROPOSED_PAGES = 3
EVIDENCE_CHARACTERS = 220
MIN_TOKEN_LENGTH = 2
STRONG_COVERAGE = 0.34

FRONT_MATTER_MARKERS = ("발행처", "발행인", "편집인", "편집장", "ISSN")
FRONT_MATTER_HITS = 2
"""A masthead repeats the title and author, so it matches any title-derived
answer while containing no evidence. Pages carrying several colophon markers are
excluded from proposals."""

STOPWORDS = frozenset(
    {
        "그리고",
        "또는",
        "위한",
        "위해",
        "통해",
        "대한",
        "대해",
        "있는",
        "있다",
        "없다",
        "이다",
        "된다",
        "하는",
        "한다",
        "등의",
        "등을",
        "따라",
        "관련",
        "경우",
        "수행",
        "필요",
        "가능",
        "다양",
        "주요",
        "확보",
        "강화",
        "개선",
        "방안",
        "방향",
        "제고",
    }
)

OUTPUT_COLUMNS = (
    "case_id",
    "prior_case_id",
    "query_slice",
    "question",
    "expected_evidence",
    "relevant_publication_ids",
    "document_title",
    "page_count",
    "proposed_pages",
    "proposal_coverage",
    "proposal_confidence",
    "proposed_page_text",
    "relevant_pages",
    "relevance_grade",
    "reviewed_by",
    "decision_basis",
    "notes",
)


def _sanitize(text: str) -> str:
    stripped = "".join(" " if unicodedata.category(ch) in {"Cc", "Cf"} else ch for ch in text)
    return " ".join(stripped.split())


def _tokens(text: str) -> set[str]:
    folded = unicodedata.normalize("NFC", text).casefold()
    raw = re.findall(r"[0-9a-z]+|[가-힣]+", folded)
    return {t for t in raw if len(t) >= MIN_TOKEN_LENGTH and t not in STOPWORDS}


def _documents() -> dict[str, tuple[list[Any], str]]:
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


def _propose(answer: str, pages: list[Any]) -> tuple[list[int], float, str, str]:
    """Rank pages by how much of the expected answer's vocabulary they contain."""
    target = _tokens(answer)
    if not target or not pages:
        return [], 0.0, "none", ""

    scored: list[tuple[float, int, Any]] = []
    for page in pages:
        text = _sanitize(page.text)
        if sum(1 for marker in FRONT_MATTER_MARKERS if marker in text) >= FRONT_MATTER_HITS:
            continue
        present = _tokens(text) & target
        if present:
            scored.append((-len(present) / len(target), page.page_number, page))
    if not scored:
        return [], 0.0, "none", ""
    scored.sort(key=lambda item: (item[0], item[1]))

    chosen = scored[:MAX_PROPOSED_PAGES]
    coverage = -chosen[0][0]
    confidence = "strong" if coverage >= STRONG_COVERAGE else "weak"
    numbers = [number for _, number, _ in chosen]
    excerpt = "  ||  ".join(
        f"p{number}: {_sanitize(page.text)[:EVIDENCE_CHARACTERS]}…" for _, number, page in chosen
    )
    return numbers, coverage, confidence, excerpt


def main() -> None:
    for required in (DRAFTS_PATH, QUESTION_DECISIONS_PATH):
        if not required.exists():
            raise SystemExit(f"{required.name} not found.")

    before = corpus_digest(DATA_DIRECTORY)
    drafts = {row["case_id"]: row for row in csv.DictReader(DRAFTS_PATH.open(encoding="utf-8-sig"))}
    accepted = [
        row
        for row in csv.DictReader(QUESTION_DECISIONS_PATH.open(encoding="utf-8-sig"))
        if (row.get("question_verdict") or "").strip().lower() == "accept"
        and row["query_slice"] != "abstention"
    ]
    documents = _documents()

    rows: list[dict[str, str]] = []
    confidence_counts: Counter[str] = Counter()

    for decision in accepted:
        draft = drafts.get(decision["case_id"], {})
        publication_id = decision["relevant_publication_ids"]
        pages, title = documents.get(publication_id, ([], ""))
        answer = draft.get("expected_evidence", "")
        numbers, coverage, confidence, excerpt = _propose(answer, pages)
        confidence_counts[confidence] += 1

        rows.append(
            {
                "case_id": decision["case_id"],
                "prior_case_id": decision["prior_case_id"],
                "query_slice": decision["query_slice"],
                "question": decision["question"],
                "expected_evidence": answer,
                "relevant_publication_ids": publication_id,
                "document_title": title,
                "page_count": str(len(pages)),
                "proposed_pages": ",".join(str(n) for n in numbers),
                "proposal_coverage": f"{coverage:.2f}",
                "proposal_confidence": confidence,
                "proposed_page_text": excerpt,
                "relevant_pages": "",
                "relevance_grade": "",
                "reviewed_by": "",
                "decision_basis": "",
                "notes": decision.get("notes", ""),
            }
        )

    rows.sort(key=lambda row: (row["proposal_confidence"] != "strong", row["case_id"]))
    output_path = OUTPUT_DIRECTORY / "golden_accepted_page_review.csv"
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "corpus_snapshot_digest": before,
        "accepted_non_abstention_rows": len(rows),
        "proposal_confidence": dict(sorted(confidence_counts.items())),
        "scoring_signal": "expected_answer vocabulary overlap per page",
        "why_not_the_question": (
            "Ranking by question keywords would reproduce the signal lexical "
            "retrieval uses at query time, making the benchmark measure itself."
        ),
        "boundary": (
            "proposed_pages is a proposal. relevant_pages stays empty until a "
            "reviewer confirms it, per EVALUATION.md rule 5."
        ),
    }
    (OUTPUT_DIRECTORY / "golden_accepted_page_report.json").write_text(
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
