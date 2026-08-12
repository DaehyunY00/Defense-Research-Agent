"""Human review decision boundary tests.

The load path guards an approval boundary, so its failure modes matter more than
its happy path. A misspelled decision must stop the run rather than be guessed
at, and only an explicit approval with a confirmed title may promote a
publication.
"""

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import PublicationType, ResearchPublication
from defense_research_agent.human_review import (
    HUMAN_REVIEW_TITLE_SOURCE,
    PublicationReviewDecision,
    ReviewDecision,
    ReviewDecisionSet,
    apply_review_decisions,
    load_review_decisions,
)

COLUMNS = (
    "publication_id",
    "suggested_title",
    "decision",
    "confirmed_title",
    "reviewer_notes",
)


def _write(path: Path, rows: list[tuple[str, str, str, str, str]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    return path


def _publication(publication_id: str = "pub-1") -> ResearchPublication:
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
        title="2024_박원호_군사훈련으로인한민군갈등",
        raw_metadata={"_ingestion": {"title_source": "filename", "filename_year": 2024}},
    )


def test_approved_rows_yield_confirmed_titles(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "review.csv",
        [("pub-1", "제안 제목", "approve", "표지 확정 제목", "")],
    )

    decisions = load_review_decisions(path)

    assert decisions.approved_titles == {"pub-1": "표지 확정 제목"}


def test_decision_value_is_case_insensitive(tmp_path: Path) -> None:
    path = _write(tmp_path / "review.csv", [("pub-1", "제안", " APPROVE ", "확정", "")])

    assert load_review_decisions(path).approved_titles == {"pub-1": "확정"}


def test_misspelled_decision_stops_the_load(tmp_path: Path) -> None:
    """A typo must not read as approval, and must not be guessed at."""
    path = _write(tmp_path / "review.csv", [("pub-1", "제안", "apporve", "확정", "")])

    with pytest.raises(ValueError, match="unrecognized decision"):
        load_review_decisions(path)


def test_unreviewed_rows_are_skipped(tmp_path: Path) -> None:
    """A partly completed round loads; blank rows stay held out."""
    path = _write(
        tmp_path / "review.csv",
        [("pub-1", "제안", "approve", "확정", ""), ("pub-2", "제안", "", "", "")],
    )

    decisions = load_review_decisions(path)

    assert [d.publication_id for d in decisions.decisions] == ["pub-1"]
    assert "pub-2" not in decisions.approved_titles


def test_rejected_and_deferred_rows_do_not_promote(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "review.csv",
        [
            ("pub-1", "제안", "reject", "", "본문 손상"),
            ("pub-2", "제안", "defer", "", "표지 확인 불가"),
        ],
    )

    decisions = load_review_decisions(path)

    assert decisions.approved_titles == {}
    assert len(decisions.decisions) == 2


def test_approval_without_a_confirmed_title_is_rejected(tmp_path: Path) -> None:
    """Otherwise the untrusted file-name title would be re-admitted."""
    path = _write(tmp_path / "review.csv", [("pub-1", "제안", "approve", "", "")])

    with pytest.raises(ValidationError, match="must record a confirmed_title"):
        load_review_decisions(path)


def test_missing_columns_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerow(["publication_id", "decision"])

    with pytest.raises(ValueError, match="missing columns"):
        load_review_decisions(path)


def test_duplicate_publication_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="must not repeat"):
        ReviewDecisionSet(
            decisions=[
                PublicationReviewDecision(
                    publication_id="pub-1",
                    decision=ReviewDecision.APPROVE,
                    confirmed_title="첫 확정",
                ),
                PublicationReviewDecision(
                    publication_id="pub-1",
                    decision=ReviewDecision.REJECT,
                ),
            ]
        )


# --- applying decisions ---------------------------------------------------------


def test_approved_publication_takes_the_confirmed_title() -> None:
    updated = apply_review_decisions(_publication(), {"pub-1": "표지 확정 제목"})

    assert updated.title == "표지 확정 제목"


def test_applied_lineage_marks_the_title_as_human_reviewed() -> None:
    """The quality gate stops treating the title as filename-derived."""
    updated = apply_review_decisions(_publication(), {"pub-1": "표지 확정 제목"})

    lineage = updated.raw_metadata["_ingestion"]
    assert isinstance(lineage, dict)
    assert lineage["title_source"] == HUMAN_REVIEW_TITLE_SOURCE


def test_original_filename_title_is_preserved_for_audit() -> None:
    original = _publication()

    updated = apply_review_decisions(original, {"pub-1": "표지 확정 제목"})

    lineage = updated.raw_metadata["_ingestion"]
    assert isinstance(lineage, dict)
    assert lineage["filename_title"] == original.title
    assert lineage["filename_year"] == 2024


def test_unapproved_publication_is_returned_unchanged() -> None:
    original = _publication()

    assert apply_review_decisions(original, {}) is original
    assert apply_review_decisions(original, {"other": "제목"}) is original


def test_publication_without_lineage_still_gets_one() -> None:
    bare = ResearchPublication(
        publication_id="pub-2",
        publication_type=PublicationType.RESEARCH_REPORT,
    )

    updated = apply_review_decisions(bare, {"pub-2": "확정 제목"})

    lineage = updated.raw_metadata["_ingestion"]
    assert isinstance(lineage, dict)
    assert lineage["title_source"] == HUMAN_REVIEW_TITLE_SOURCE
