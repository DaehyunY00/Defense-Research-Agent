"""CLI review and same-run resume for ranked research topics."""

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from defense_research_agent.domain.review import (
    ReviewDecisionType,
    ReviewEdits,
    ReviewSubmission,
)
from defense_research_agent.repositories import InMemoryResearchPublicationRepository
from defense_research_agent.repositories.review_history import ReviewHistoryRepository
from defense_research_agent.services.review import HumanReviewService, load_ranked_topics

_DEFAULT_ARTIFACTS_ROOT = Path("artifacts")
_DEFAULT_INDEX_PATH = Path("artifacts/normalized/publications.jsonl")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m defense_research_agent.cli.review_topics",
        description="Review ranked topics and resume planning-card generation.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifacts-root", type=Path, default=_DEFAULT_ARTIFACTS_ROOT)
    parser.add_argument("--index", type=Path, default=_DEFAULT_INDEX_PATH)
    parser.add_argument("--candidate-id")
    parser.add_argument("--decision", choices=tuple(ReviewDecisionType))
    parser.add_argument("--reviewer")
    parser.add_argument("--comment")
    parser.add_argument("--title")
    parser.add_argument("--research-question")
    parser.add_argument("--trigger")
    parser.add_argument("--novelty-claim")
    parser.add_argument("--recommended-output")
    parser.add_argument("--limitation", action="append", dest="limitations")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Record one decision or conduct an interactive review of pending candidates."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    run_id = cast(str, args.run_id)
    artifacts_root = cast(Path, args.artifacts_root)
    index_path = cast(Path, args.index)
    if not _SAFE_RUN_ID.fullmatch(run_id):
        parser.error("--run-id must be a safe path segment")
    ranked_path = artifacts_root / "runs" / run_id / "ranked_candidates.json"
    if not ranked_path.is_file():
        parser.error(f"ranking artifact does not exist: {ranked_path}")
    if not index_path.is_file():
        parser.error(f"normalized publication index does not exist: {index_path}")

    try:
        ranked_topics = load_ranked_topics(ranked_path)
        publication_repository = InMemoryResearchPublicationRepository.from_jsonl(index_path)
        history_repository = ReviewHistoryRepository(artifacts_root)
        service = HumanReviewService(history_repository, publication_repository)
    except ValueError as error:
        parser.error(str(error))

    candidate_id = cast(str | None, args.candidate_id)
    decision_value = cast(str | None, args.decision)
    if (candidate_id is None) != (decision_value is None):
        parser.error("--candidate-id and --decision must be supplied together")

    try:
        if candidate_id is not None and decision_value is not None:
            submission = _submission_from_args(args, candidate_id, decision_value)
            service.record_decision(run_id, ranked_topics, submission)
        else:
            _interactive_review(run_id, ranked_topics, service, cast(str | None, args.reviewer))
        gate = service.review_gate(run_id, ranked_topics)
        cards_path = service.write_planning_cards(artifacts_root, run_id, ranked_topics)
        _update_run_state(
            artifacts_root / "runs" / run_id / "run_state.json",
            gate.status.value,
            bool(gate.approved_candidate_ids),
            cards_path is not None,
        )
    except ValueError as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                "run_id": run_id,
                "status": gate.status.value,
                "approved_candidate_ids": gate.approved_candidate_ids,
                "pending_candidate_ids": gate.pending_candidate_ids,
                "planning_cards_path": str(cards_path) if cards_path is not None else None,
                "review_history_path": str(history_repository.path_for(run_id)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _submission_from_args(
    args: argparse.Namespace,
    candidate_id: str,
    decision_value: str,
) -> ReviewSubmission:
    decision = ReviewDecisionType(decision_value)
    reviewer = cast(str | None, args.reviewer)
    if reviewer is None or not reviewer.strip():
        raise ValueError("--reviewer is required for non-interactive review")
    edits = _edits_from_args(args)
    if decision is ReviewDecisionType.APPROVE_WITH_EDITS and edits is None:
        raise ValueError("approve_with_edits requires at least one edit argument")
    return ReviewSubmission(
        candidate_id=candidate_id,
        decision=decision,
        reviewer=reviewer,
        edits=edits,
        comment=cast(str | None, args.comment),
    )


def _edits_from_args(args: argparse.Namespace) -> ReviewEdits | None:
    values = {
        "working_title": cast(str | None, args.title),
        "research_question": cast(str | None, args.research_question),
        "trigger": cast(str | None, args.trigger),
        "novelty_claim": cast(str | None, args.novelty_claim),
        "recommended_output": cast(str | None, args.recommended_output),
        "known_limitations": cast(list[str] | None, args.limitations),
    }
    if all(value is None for value in values.values()):
        return None
    return ReviewEdits.model_validate(values)


def _interactive_review(
    run_id: str,
    ranked_topics: Sequence[object],
    service: HumanReviewService,
    reviewer_arg: str | None,
) -> None:
    from defense_research_agent.domain.ranking import RankedTopic

    typed_topics = [topic for topic in ranked_topics if isinstance(topic, RankedTopic)]
    reviewer = reviewer_arg or input("검토자 이름: ").strip()
    if not reviewer:
        raise ValueError("reviewer must not be blank")
    gate = service.review_gate(run_id, typed_topics)
    pending = set(gate.pending_candidate_ids)
    for topic in typed_topics:
        candidate = topic.candidate
        if candidate.candidate_id not in pending:
            continue
        print(
            f"[{topic.rank}] {candidate.candidate_id} | {_terminal_safe(candidate.working_title)}"
        )
        decision = ReviewDecisionType(
            input("결정 (approve/approve_with_edits/hold/reject): ").strip()
        )
        edits = None
        if decision is ReviewDecisionType.APPROVE_WITH_EDITS:
            title = input(f"수정 가제 [{_terminal_safe(candidate.working_title)}]: ").strip()
            question = input(
                f"수정 연구질문 [{_terminal_safe(candidate.research_question)}]: "
            ).strip()
            edits = ReviewEdits(
                working_title=title or None,
                research_question=question or None,
            )
        comment = input("검토 의견(선택): ").strip()
        service.record_decision(
            run_id,
            typed_topics,
            ReviewSubmission(
                candidate_id=candidate.candidate_id,
                decision=decision,
                reviewer=reviewer,
                edits=edits,
                comment=comment or None,
            ),
        )


def _update_run_state(
    path: Path,
    review_status: str,
    human_approved: bool,
    cards_generated: bool,
) -> None:
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("run state artifact must be a JSON object")
    payload["status"] = review_status
    payload["human_approved"] = human_approved
    audit = payload.get("orchestration_audit")
    if isinstance(audit, dict):
        audit["resumed_after_interrupt"] = True
        statuses = audit.get("node_statuses")
        if isinstance(statuses, dict):
            statuses["human_review_interrupt"] = "success"
            statuses["generate_topic_planning_cards"] = "success" if cards_generated else "not_run"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _terminal_safe(value: str) -> str:
    return "".join(character if character.isprintable() else "�" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
