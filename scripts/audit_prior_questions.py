"""Flag prior-study questions that need scrutiny before becoming golden labels.

The imported dataset mixes human-authored and model-generated questions, and no
per-question provenance was recorded. ``EVALUATION.md`` forbids using an
unreviewed model-generated label as golden truth, so every row needs review —
but not every row needs the same amount of attention.

This script computes objective signals so a reviewer can start with the rows
most likely to be wrong. It does not decide anything; it sorts the work.

Signals, and why each one matters:

``answer_grounding``
    Share of the expected answer's content words that actually appear in the
    mapped document. A low score means the answer may have been written from
    the model's own knowledge rather than from the source, which makes the pair
    unusable as a retrieval label.

``answer_leak``
    The question already contains the answer, usually as a parenthetical gloss:
    "MUM-T(유무인복합체계)란 무엇인가?" A retriever can match on the gloss, so the
    question tests string matching rather than retrieval.

``templated``
    Formulaic phrasing that suggests bulk generation rather than a researcher's
    actual question.

``near_duplicate``
    Another case asks substantially the same thing. Duplicates inflate whichever
    retrieval behaviour they happen to favour.

``trap_topic_present``
    An abstention case whose *question* terms are well covered by the corpus.
    The topic being present does not make the specific fact present, but it
    raises the chance that the question is answerable after all, so a reviewer
    should confirm the fact is genuinely absent.

    An earlier version of this audit compared the abstention *answer* text
    against the corpus and flagged all fifty cases. That was wrong: those
    answers are meta-statements of the form "그 정보는 문서에 없습니다. 문서에서는
    X를 다루고 있습니다", so their words necessarily appear in the corpus.
    Grounding is not defined for them.

Read-only over ``data/``. Output goes to ``artifacts/human_review/``.

Run:  uv run python scripts/audit_prior_questions.py
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
DECISIONS_PATH = OUTPUT_DIRECTORY / "golden_question_decisions.csv"
"""Reviewer-authored verdicts. No script writes this file.

The generated audit and the human decisions are separate files on purpose. An
earlier generator overwrote a completed review with an empty file because the
queue it regenerated had become empty."""
REVIEW_DECISIONS_PATH = OUTPUT_DIRECTORY / "manual_review_decisions.csv"

GROUNDING_FLAG_THRESHOLD = 0.5
NEAR_DUPLICATE_THRESHOLD = 0.7
MIN_TOKEN_LENGTH = 2

TEMPLATE_PATTERNS = (
    r"종합하시오\.?$",
    r"도출하시오\.?$",
    r"분석하시오\.?$",
    r"논하시오\.?$",
    r"설명하시오\.?$",
    r"제시하시오\.?$",
)

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
        "무엇",
        "어떻게",
        "어떤",
        "이란",
        "인가",
        "인가요",
        "방안",
        "방향",
    }
)

OUTPUT_COLUMNS = (
    "risk_rank",
    "case_id",
    "prior_case_id",
    "query_slice",
    "question",
    "expected_evidence",
    "relevant_publication_ids",
    "answer_grounding",
    "review_flags",
    "near_duplicate_of",
    "question_verdict",
    "revised_question",
    "reviewed_by",
    "notes",
)


def _sanitize(text: str) -> str:
    stripped = "".join(" " if unicodedata.category(ch) in {"Cc", "Cf"} else ch for ch in text)
    return " ".join(stripped.split())


def _tokens(text: str) -> set[str]:
    """Content tokens: Hangul or alphanumeric runs, stopwords removed."""
    folded = unicodedata.normalize("NFC", text).casefold()
    raw = re.findall(r"[0-9a-z]+|[가-힣]+", folded)
    return {token for token in raw if len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS}


def _document_text() -> dict[str, str]:
    outcome = IngestionService().ingest(
        DATA_DIRECTORY,
        OUTPUT_DIRECTORY / "normalized",
        OUTPUT_DIRECTORY / "ingestion_report.json",
    )
    approved: dict[str, str] = {}
    if REVIEW_DECISIONS_PATH.exists():
        approved = load_review_decisions(REVIEW_DECISIONS_PATH).approved_titles

    parser = JsonPageParser()
    texts: dict[str, str] = {}
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
        body = " ".join(_sanitize(page.text) for page in parsed.pages)
        texts[reviewed.publication_id] = unicodedata.normalize("NFC", body).casefold()
    return texts


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def main() -> None:
    if not DRAFTS_PATH.exists():
        raise SystemExit(f"{DRAFTS_PATH.name} not found. Run the import script first.")

    before = corpus_digest(DATA_DIRECTORY)
    drafts = list(csv.DictReader(DRAFTS_PATH.open(encoding="utf-8-sig")))
    documents = _document_text()
    corpus_text = " ".join(documents.values())

    question_tokens = {row["case_id"]: _tokens(row["question"]) for row in drafts}

    rows: list[dict[str, Any]] = []
    flag_counts: Counter[str] = Counter()

    for row in drafts:
        flags: list[str] = []
        answer = row["expected_evidence"]
        question = row["question"]
        publication_id = row["relevant_publication_ids"]
        is_abstention = row["acceptable_abstention"] == "yes"

        grounding = ""
        # Grounding compares a factual answer against its source. An abstention
        # answer asserts absence, so there is nothing to ground.
        if not is_abstention and publication_id and publication_id in documents and answer:
            answer_tokens = _tokens(answer)
            if answer_tokens:
                present = sum(1 for t in answer_tokens if t in documents[publication_id])
                ratio = present / len(answer_tokens)
                grounding = f"{ratio:.2f}"
                if ratio < GROUNDING_FLAG_THRESHOLD:
                    flags.append("low_grounding")

        answer_tokens = _tokens(answer)
        if not is_abstention:
            leaked = answer_tokens & question_tokens[row["case_id"]]
            if answer_tokens and len(leaked) / len(answer_tokens) > 0.5:
                flags.append("answer_leak")

        if any(re.search(pattern, question.strip()) for pattern in TEMPLATE_PATTERNS):
            flags.append("templated")

        if is_abstention:
            asked = question_tokens[row["case_id"]]
            if asked:
                present = sum(1 for token in asked if token in corpus_text)
                if present / len(asked) > 0.9:
                    flags.append("trap_topic_present")

        if not publication_id and not is_abstention:
            flags.append("unmapped_document")

        for flag in flags:
            flag_counts[flag] += 1

        rows.append(
            {
                "case_id": row["case_id"],
                "prior_case_id": row["prior_case_id"],
                "query_slice": row["query_slice"],
                "question": question,
                "expected_evidence": answer,
                "relevant_publication_ids": publication_id,
                "answer_grounding": grounding,
                "review_flags": ";".join(flags),
                "near_duplicate_of": "",
                "question_verdict": "",
                "revised_question": "",
                "reviewed_by": "",
                "notes": row["notes"],
                "_flag_count": len(flags),
            }
        )

    # Near-duplicate detection over question tokens.
    for index, row in enumerate(rows):
        left = question_tokens[row["case_id"]]
        partners = [
            other["case_id"]
            for other in rows[index + 1 :]
            if _jaccard(left, question_tokens[other["case_id"]]) >= NEAR_DUPLICATE_THRESHOLD
        ]
        if partners:
            row["near_duplicate_of"] = ";".join(partners)
            row["review_flags"] = ";".join(filter(None, [row["review_flags"], "near_duplicate"]))
            row["_flag_count"] += 1
            flag_counts["near_duplicate"] += 1

    rows.sort(key=lambda item: (-item["_flag_count"], item["case_id"]))
    for rank, row in enumerate(rows, start=1):
        row["risk_rank"] = rank
        row.pop("_flag_count")

    if DECISIONS_PATH.exists():
        decided = {
            row["case_id"]
            for row in csv.DictReader(DECISIONS_PATH.open(encoding="utf-8-sig"))
            if (row.get("question_verdict") or "").strip()
        }
        for row in rows:
            if row["case_id"] in decided:
                row["question_verdict"] = "(decided)"

    output_path = OUTPUT_DIRECTORY / "golden_question_audit.csv"
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    flagged = sum(1 for row in rows if row["review_flags"])
    report = {
        "corpus_snapshot_digest": before,
        "audited_rows": len(rows),
        "rows_with_any_flag": flagged,
        "rows_without_flag": len(rows) - flagged,
        "flag_counts": dict(sorted(flag_counts.items())),
        "grounding_threshold": GROUNDING_FLAG_THRESHOLD,
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
        "note": (
            "Flags rank the review queue. They are not verdicts. An unflagged "
            "question still needs a reviewer, because the dataset mixes "
            "human-authored and model-generated items with no recorded provenance."
        ),
    }
    (OUTPUT_DIRECTORY / "golden_question_audit_report.json").write_text(
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
