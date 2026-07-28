"""Tests for independent structured evaluator boundaries."""

import json

import pytest

from defense_research_agent.agents import (
    EvaluationValidationError,
    FakeModelGateway,
    NoveltyEvaluator,
    PolicyRelevanceEvaluator,
)
from defense_research_agent.domain import (
    CandidateEvaluationInput,
    EvaluationCriterion,
    PublicationType,
    RecommendedOutputType,
    ResearchPublication,
    TopicCandidate,
    TopicSignal,
)


def _candidate(title: str = "국방 AI 성과평가") -> TopicCandidate:
    return TopicCandidate(
        candidate_id="candidate:evaluator",
        working_title=title,
        research_question="최근 정책 변화 이후 성과를 어떻게 검증할 것인가?",
        trigger="정부 시행계획 공개",
        internal_context="기존 국방 AI 연구와 연결한다.",
        novelty_claim="집행 성과지표를 새롭게 검토한다.",
        recommended_output=RecommendedOutputType.DEFENSE_POLICY_RESEARCH,
        supporting_signal_ids=["signal:official"],
        related_publication_ids=["pub:prior"],
        known_limitations=["비공개 성과자료가 제한된다."],
    )


def _evaluation_input(title: str = "기존 연구 제목") -> CandidateEvaluationInput:
    return CandidateEvaluationInput(
        candidate=_candidate(title),
        signals=[
            TopicSignal(
                signal_id="signal:official",
                signal_type="external_government_policy",
                title="국방 AI 시행계획",
                confidence=0.95,
                source_ids=["source:official"],
            )
        ],
        related_publications=[
            ResearchPublication(
                publication_id="pub:prior",
                publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
                title="기존 연구 제목",
            )
        ],
        similar_publications=[
            ResearchPublication(
                publication_id="pub:prior",
                publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
                title="기존 연구 제목",
            )
        ],
    )


def test_policy_evaluator_receives_no_peer_results_and_preserves_untrusted_boundary() -> None:
    gateway = FakeModelGateway(
        [
            {
                "results": [
                    {
                        "candidate_id": "candidate:evaluator",
                        "criterion": criterion,
                        "score": 80,
                        "rationale": "정책 문제와 시급성이 명확하다.",
                        "evidence_ids": ["signal:official", "pub:prior"],
                        "risks": [],
                        "confidence": 0.8,
                    }
                    for criterion in (
                        "policy_relevance",
                        "timeliness",
                        "policy_impact",
                    )
                ]
            }
        ]
    )
    evaluator = PolicyRelevanceEvaluator(gateway)

    results = evaluator.evaluate(_evaluation_input())

    assert {result.criterion for result in results} == set(evaluator.criteria)
    payload = json.loads(gateway.calls[0].messages[1].content)
    assert "peer_results" not in payload
    assert "evaluation_results" not in payload
    assert "untrusted_external_signals" in payload
    assert "ignore" in gateway.calls[0].messages[0].content.casefold()


def test_evaluator_blocks_unknown_evidence_id() -> None:
    gateway = FakeModelGateway(
        [
            {
                "results": [
                    {
                        "candidate_id": "candidate:evaluator",
                        "criterion": "policy_relevance",
                        "score": 80,
                        "rationale": "알 수 없는 근거를 인용했다.",
                        "evidence_ids": ["pub:invented"],
                        "confidence": 0.8,
                    }
                ]
            }
        ]
    )

    with pytest.raises(EvaluationValidationError, match="unknown evidence"):
        PolicyRelevanceEvaluator(gateway).evaluate(_evaluation_input())


def test_novelty_evaluator_caps_direct_publication_title_duplicate() -> None:
    gateway = FakeModelGateway(
        [
            {
                "results": [
                    {
                        "candidate_id": "candidate:evaluator",
                        "criterion": "novelty",
                        "score": 95,
                        "rationale": "모델은 신규성이 높다고 판단했다.",
                        "evidence_ids": [],
                        "confidence": 0.9,
                    }
                ]
            }
        ]
    )

    result = NoveltyEvaluator(gateway).evaluate(_evaluation_input())[0]

    assert result.criterion is EvaluationCriterion.NOVELTY
    assert result.score == 20
    assert result.evidence_ids == ["pub:prior"]
    assert "direct_duplicate_detected" in result.risks
