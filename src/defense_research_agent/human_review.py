"""Human review decisions that promote held-out publications into the index.

The quality gate holds DQ-04 documents in ``manual_review`` because their title
can only be trusted from the cover, not the file name. A reviewer confirms the
cover title and records a decision. This module loads those decisions and
applies them.

Two rules govern this boundary, both from AGENTS.md:

- Only an explicit ``approve`` with a confirmed title promotes a publication.
  Anything else leaves it held out.
- An unrecognized decision value fails the load. A typo must never be read as
  silent approval, and it must never be guessed at.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from defense_research_agent.domain.common import DomainModel, EntityId, Label
from defense_research_agent.domain.publication import ResearchPublication

HUMAN_REVIEW_TITLE_SOURCE = "human_review"
"""``_ingestion.title_source`` written for a reviewer-confirmed title.

The quality gate treats any source other than ``filename`` as a resolved title,
so a confirmed publication stops matching the DQ-04 review branch.
"""

REQUIRED_COLUMNS = frozenset({"publication_id", "decision", "confirmed_title", "suggested_title"})


class ReviewDecision(StrEnum):
    """Outcome a reviewer may record for a held-out publication."""

    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"


class PublicationReviewDecision(DomainModel):
    """One reviewer decision about one held-out publication."""

    publication_id: EntityId
    decision: ReviewDecision
    confirmed_title: Label | None = None
    reviewer_notes: str | None = None

    @model_validator(mode="after")
    def approval_requires_a_confirmed_title(self) -> PublicationReviewDecision:
        """An approval without a title would re-admit the untrusted file name."""
        if self.decision is ReviewDecision.APPROVE and not self.confirmed_title:
            raise ValueError("an approved publication must record a confirmed_title")
        return self


class ReviewDecisionSet(DomainModel):
    """All decisions loaded from one review round."""

    decisions: list[PublicationReviewDecision] = Field(default_factory=list)

    @model_validator(mode="after")
    def publication_ids_must_be_unique(self) -> ReviewDecisionSet:
        """Two conflicting decisions for one publication must not be merged."""
        ids = [decision.publication_id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("publication_id must not repeat within a decision set")
        return self

    @property
    def approved_titles(self) -> dict[str, str]:
        """Confirmed titles keyed by publication, for approved rows only."""
        return {
            decision.publication_id: decision.confirmed_title
            for decision in self.decisions
            if decision.decision is ReviewDecision.APPROVE and decision.confirmed_title
        }


def load_review_decisions(path: Path) -> ReviewDecisionSet:
    """Read a completed review CSV.

    Rows with an empty ``decision`` are unreviewed and are skipped, so a partly
    completed round loads cleanly. An unrecognized value raises instead, because
    a misspelling is indistinguishable from a rejection and guessing at intent
    on an approval boundary is not acceptable.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"review file is missing columns: {sorted(missing)}")

        decisions: list[PublicationReviewDecision] = []
        for line_number, row in enumerate(reader, start=2):
            raw_decision = (row.get("decision") or "").strip().lower()
            if not raw_decision:
                continue
            if raw_decision not in set(ReviewDecision):
                allowed = ", ".join(sorted(item.value for item in ReviewDecision))
                raise ValueError(
                    f"line {line_number}: unrecognized decision {raw_decision!r}; "
                    f"expected one of {allowed}"
                )
            confirmed = (row.get("confirmed_title") or "").strip()
            notes = (row.get("reviewer_notes") or "").strip()
            decisions.append(
                PublicationReviewDecision(
                    publication_id=(row.get("publication_id") or "").strip(),
                    decision=ReviewDecision(raw_decision),
                    confirmed_title=confirmed or None,
                    reviewer_notes=notes or None,
                )
            )
    return ReviewDecisionSet(decisions=decisions)


def apply_review_decisions(
    publication: ResearchPublication,
    approved_titles: Mapping[str, str],
) -> ResearchPublication:
    """Return the publication with a reviewer-confirmed title, if one exists.

    The ingestion lineage is rewritten to say the title came from human review
    rather than the file name. Nothing else about the publication changes, and
    the original file-name reading stays in ``_ingestion`` for audit.
    """
    confirmed = approved_titles.get(publication.publication_id)
    if confirmed is None:
        return publication

    lineage = publication.raw_metadata.get("_ingestion")
    updated_lineage = dict(lineage) if isinstance(lineage, Mapping) else {}
    updated_lineage["title_source"] = HUMAN_REVIEW_TITLE_SOURCE
    updated_lineage["filename_title"] = publication.title
    raw_metadata = dict(publication.raw_metadata)
    raw_metadata["_ingestion"] = updated_lineage

    return publication.model_copy(update={"title": confirmed, "raw_metadata": raw_metadata})
