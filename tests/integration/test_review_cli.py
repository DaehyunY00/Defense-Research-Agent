"""Integration tests for the local human-review CLI."""

import json
import subprocess
import sys
from pathlib import Path

from defense_research_agent.domain import (
    CandidateAttributes,
    PublicationType,
    RankedTopic,
    RecommendedOutputType,
    ResearchHorizon,
    ResearchPublication,
    TopicCandidate,
)
from defense_research_agent.services.ranking import (
    load_ranking_config,
    write_ranked_candidates,
)

PROJECT_ROOT = Path(__file__).parents[2]


def _prepare_run(tmp_path: Path) -> tuple[Path, Path]:
    artifacts_root = tmp_path / "artifacts"
    index_path = artifacts_root / "normalized" / "publications.jsonl"
    index_path.parent.mkdir(parents=True)
    publication = ResearchPublication(
        publication_id="pub:cli-review",
        publication_type=PublicationType.DEFENSE_FORUM,
        title="관련 국방논단",
    )
    index_path.write_text(publication.model_dump_json() + "\n", encoding="utf-8")
    candidate = TopicCandidate(
        candidate_id="candidate:cli-review",
        working_title="CLI 검토 후보",
        research_question="CLI에서 이 후보를 승인할 것인가?",
        recommended_output=RecommendedOutputType.DEFENSE_FORUM,
        related_publication_ids=["pub:cli-review"],
    )
    ranked = RankedTopic(
        candidate=candidate,
        rank=1,
        criterion_scores={"public_evidence_sufficiency": 70},
        raw_score=70,
        penalized_score=70,
        adjusted_score=70,
        confidence=0.8,
        evidence_ids=["pub:cli-review"],
        attributes=CandidateAttributes(
            output_type=RecommendedOutputType.DEFENSE_FORUM,
            research_horizon=ResearchHorizon.SHORT_TERM,
        ),
        explanation=["테스트 점수"],
    )
    config = load_ranking_config(PROJECT_ROOT / "configs" / "scoring.json")
    write_ranked_candidates(artifacts_root, "cli-review-run", [ranked], config)
    return artifacts_root, index_path


def test_review_cli_approves_and_writes_append_only_history_and_card(
    tmp_path: Path,
) -> None:
    artifacts_root, index_path = _prepare_run(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent.cli.review_topics",
            "--run-id",
            "cli-review-run",
            "--artifacts-root",
            str(artifacts_root),
            "--index",
            str(index_path),
            "--candidate-id",
            "candidate:cli-review",
            "--decision",
            "approve",
            "--reviewer",
            "통합테스트 연구자",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["status"] == "ready_for_cards"
    assert (artifacts_root / "runs" / "cli-review-run" / "review_history.jsonl").is_file()
    assert (artifacts_root / "runs" / "cli-review-run" / "topic_planning_cards.json").is_file()


def test_review_cli_rejects_unknown_candidate_id(tmp_path: Path) -> None:
    artifacts_root, index_path = _prepare_run(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent.cli.review_topics",
            "--run-id",
            "cli-review-run",
            "--artifacts-root",
            str(artifacts_root),
            "--index",
            str(index_path),
            "--candidate-id",
            "candidate:unknown",
            "--decision",
            "approve",
            "--reviewer",
            "통합테스트 연구자",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "unknown candidate_id" in completed.stderr
