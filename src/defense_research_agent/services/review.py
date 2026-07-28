"""Human review gate, approved edits, and deterministic planning cards."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from defense_research_agent.domain.evaluation import EvaluationCriterion
from defense_research_agent.domain.publication import PublicationType
from defense_research_agent.domain.ranking import RankedTopic
from defense_research_agent.domain.review import (
    EvidenceReference,
    ReviewDecisionType,
    ReviewEvent,
    ReviewGateResult,
    ReviewSubmission,
    ReviewWorkflowStatus,
    TopicPlanningCard,
)
from defense_research_agent.domain.topic import (
    RecommendedOutputType,
    TopicCandidate,
    TopicSignal,
)
from defense_research_agent.path_safety import ensure_outside_read_only_data
from defense_research_agent.repositories.base import ResearchPublicationRepository
from defense_research_agent.repositories.review_history import ReviewHistoryRepository


class HumanReviewService:
    """Record human decisions and generate cards only after the review gate opens."""

    def __init__(
        self,
        history_repository: ReviewHistoryRepository,
        publication_repository: ResearchPublicationRepository,
        signals: Sequence[TopicSignal] = (),
    ) -> None:
        self._history_repository = history_repository
        self._publication_repository = publication_repository
        self._signal_by_id = {signal.signal_id: signal for signal in signals}

    def record_decision(
        self,
        run_id: str,
        ranked_topics: Sequence[RankedTopic],
        submission: ReviewSubmission,
        *,
        reviewed_at: datetime | None = None,
    ) -> ReviewEvent:
        """Validate candidate scope before appending one review event."""
        candidate_ids = {topic.candidate.candidate_id for topic in ranked_topics}
        if submission.candidate_id not in candidate_ids:
            raise ValueError(f"unknown candidate_id for run {run_id}")
        return self._history_repository.append(
            run_id,
            submission,
            reviewed_at=reviewed_at,
        )

    def review_gate(
        self,
        run_id: str,
        ranked_topics: Sequence[RankedTopic],
    ) -> ReviewGateResult:
        """Derive the latest decision per candidate without changing history."""
        candidate_ids = [topic.candidate.candidate_id for topic in ranked_topics]
        latest_by_candidate: dict[str, ReviewEvent] = {}
        for event in self._history_repository.load(run_id):
            if event.candidate_id in candidate_ids:
                latest_by_candidate[event.candidate_id] = event
        pending = [
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id not in latest_by_candidate
            or latest_by_candidate[candidate_id].decision is ReviewDecisionType.HOLD
        ]
        approved = [
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id in latest_by_candidate
            and latest_by_candidate[candidate_id].decision
            in {
                ReviewDecisionType.APPROVE,
                ReviewDecisionType.APPROVE_WITH_EDITS,
            }
        ]
        if pending:
            status = ReviewWorkflowStatus.AWAITING_REVIEW
        elif approved:
            status = ReviewWorkflowStatus.READY_FOR_CARDS
        else:
            status = ReviewWorkflowStatus.COMPLETED_WITHOUT_APPROVAL
        return ReviewGateResult(
            run_id=run_id,
            status=status,
            latest_events=[
                latest_by_candidate[candidate_id]
                for candidate_id in candidate_ids
                if candidate_id in latest_by_candidate
            ],
            approved_candidate_ids=approved,
            pending_candidate_ids=pending,
        )

    def generate_planning_cards(
        self,
        run_id: str,
        ranked_topics: Sequence[RankedTopic],
    ) -> list[TopicPlanningCard]:
        """Generate cards only when all non-held decisions are complete and approved."""
        gate = self.review_gate(run_id, ranked_topics)
        if gate.status is not ReviewWorkflowStatus.READY_FOR_CARDS:
            return []
        event_by_candidate = {event.candidate_id: event for event in gate.latest_events}
        topic_by_id = {topic.candidate.candidate_id: topic for topic in ranked_topics}
        cards: list[TopicPlanningCard] = []
        for candidate_id in gate.approved_candidate_ids:
            ranked_topic = topic_by_id[candidate_id]
            candidate = _apply_approved_edits(
                ranked_topic.candidate,
                event_by_candidate[candidate_id],
            )
            cards.append(self._to_card(candidate, ranked_topic))
        return cards

    def write_planning_cards(
        self,
        artifacts_root: Path,
        run_id: str,
        ranked_topics: Sequence[RankedTopic],
    ) -> Path | None:
        """Write final cards only when the gate is ready; never emit pending output."""
        cards = self.generate_planning_cards(run_id, ranked_topics)
        if not cards:
            return None
        output_path = artifacts_root / "runs" / run_id / "topic_planning_cards.json"
        ensure_outside_read_only_data(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "topic_planning_cards": [card.model_dump(mode="json") for card in cards],
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return output_path

    def _to_card(
        self,
        candidate: TopicCandidate,
        ranked_topic: RankedTopic,
    ) -> TopicPlanningCard:
        publications = [
            publication
            for publication_id in candidate.related_publication_ids
            if (publication := self._publication_repository.get_by_id(publication_id)) is not None
        ]
        internal_references = [
            EvidenceReference(
                evidence_id=publication.publication_id,
                title=publication.title,
                evidence_type=publication.publication_type.value,
            )
            for publication in publications
        ]
        external_references = [
            EvidenceReference(
                evidence_id=signal_id,
                title=(
                    self._signal_by_id[signal_id].title if signal_id in self._signal_by_id else None
                ),
                evidence_type=(
                    self._signal_by_id[signal_id].signal_type
                    if signal_id in self._signal_by_id
                    else "external_signal"
                ),
            )
            for signal_id in candidate.supporting_signal_ids
        ]
        evidence_score = ranked_topic.criterion_scores.get(
            EvaluationCriterion.PUBLIC_EVIDENCE_SUFFICIENCY.value
        )
        return TopicPlanningCard(
            candidate_id=candidate.candidate_id,
            working_title=candidate.working_title,
            research_question=candidate.research_question,
            trigger=candidate.trigger,
            why_now=candidate.trigger,
            related_defense_forum=_references_of_type(
                publications,
                PublicationType.DEFENSE_FORUM,
            ),
            related_kida_brief=_references_of_type(
                publications,
                PublicationType.KIDA_BRIEF,
            ),
            related_defense_policy_research=_references_of_type(
                publications,
                PublicationType.DEFENSE_POLICY_RESEARCH,
            ),
            novelty=candidate.novelty_claim,
            internal_evidence=internal_references,
            external_evidence=external_references,
            evaluation_scores=ranked_topic.criterion_scores,
            evidence_sufficiency=_evidence_sufficiency_label(evidence_score),
            recommended_output=candidate.recommended_output,
            expected_outline=_expected_outline(candidate.recommended_output),
            known_limitations=candidate.known_limitations,
            further_research=_further_research(candidate, external_references),
        )


def load_ranked_topics(path: Path) -> list[RankedTopic]:
    """Load a ranking artifact for CLI review and same-run resumption."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ranked candidate artifact must be a JSON object")
    raw_topics = payload.get("ranked_candidates")
    if not isinstance(raw_topics, list):
        raise ValueError("ranked candidate artifact has no ranked_candidates list")
    return [RankedTopic.model_validate(topic) for topic in raw_topics]


def _apply_approved_edits(
    candidate: TopicCandidate,
    event: ReviewEvent,
) -> TopicCandidate:
    if event.decision is not ReviewDecisionType.APPROVE_WITH_EDITS:
        return candidate
    if event.edits is None:
        raise ValueError("approved edit event is missing edits")
    updates = event.edits.model_dump(exclude_none=True)
    return TopicCandidate.model_validate(
        {
            **candidate.model_dump(mode="python"),
            **updates,
        }
    )


def _references_of_type(
    publications: Sequence[object],
    publication_type: PublicationType,
) -> list[EvidenceReference]:
    from defense_research_agent.domain.publication import ResearchPublication

    return [
        EvidenceReference(
            evidence_id=publication.publication_id,
            title=publication.title,
            evidence_type=publication.publication_type.value,
        )
        for publication in publications
        if isinstance(publication, ResearchPublication)
        and publication.publication_type is publication_type
    ]


def _evidence_sufficiency_label(score: float | None) -> str:
    if score is None:
        return "평가 불가"
    if score >= 70:
        return "충분"
    if score >= 40:
        return "제한적"
    return "부족"


def _expected_outline(
    output_type: RecommendedOutputType | None,
) -> list[str]:
    outlines: Mapping[RecommendedOutputType, list[str]] = {
        RecommendedOutputType.DEFENSE_FORUM: [
            "문제 제기",
            "최근 변화와 기존 연구",
            "정책 대안",
            "결론",
        ],
        RecommendedOutputType.KIDA_BRIEF: [
            "핵심 이슈",
            "근거와 분석",
            "정책 시사점",
        ],
        RecommendedOutputType.DEFENSE_POLICY_RESEARCH: [
            "서론",
            "이론·제도적 배경",
            "분석 범위와 방법",
            "분석 결과",
            "정책적 함의",
        ],
        RecommendedOutputType.RESEARCH_REPORT: [
            "연구 개요",
            "선행연구",
            "자료와 방법",
            "사례·실증 분석",
            "정책 대안",
            "결론",
        ],
    }
    if output_type is None:
        return ["문제 제기", "분석", "정책 시사점"]
    return outlines.get(output_type, ["문제 제기", "분석", "정책 시사점"])


def _further_research(
    candidate: TopicCandidate,
    external_references: Sequence[EvidenceReference],
) -> list[str]:
    items = list(candidate.known_limitations)
    if not external_references:
        items.append("최근 외부 공식자료와 정책 변화 여부를 추가 확인해야 한다.")
    return list(dict.fromkeys(items))
