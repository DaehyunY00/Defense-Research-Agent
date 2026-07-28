"""Tests for weighted ranking, penalties, diversity, and reproducibility."""

from pathlib import Path

import pytest

from defense_research_agent.domain import (
    AggregatedCandidateEvaluation,
    EvaluationCriterion,
    RankingConfig,
    RecommendedOutputType,
    TopicCandidate,
    TopicSignal,
)
from defense_research_agent.services.ranking import (
    diversify_candidates,
    load_ranking_config,
    rank_candidates,
    write_ranked_candidates,
)

PROJECT_ROOT = Path(__file__).parents[3]


def _config() -> RankingConfig:
    return load_ranking_config(PROJECT_ROOT / "configs" / "scoring.json")


def _candidate(
    candidate_id: str,
    title: str,
    *,
    output: RecommendedOutputType = RecommendedOutputType.KIDA_BRIEF,
    signal_id: str = "signal:official",
) -> TopicCandidate:
    return TopicCandidate(
        candidate_id=candidate_id,
        working_title=title,
        research_question=f"{title}의 정책 대안을 어떻게 설계할 것인가?",
        recommended_output=output,
        supporting_signal_ids=[signal_id],
        related_publication_ids=["pub:prior"],
    )


def _aggregate(
    candidate_id: str,
    score: float,
    *,
    confidence: float = 0.8,
    risks: list[str] | None = None,
    missing: list[EvaluationCriterion] | None = None,
) -> AggregatedCandidateEvaluation:
    missing_set = set(missing or [])
    criterion_scores = {
        criterion.value: score for criterion in EvaluationCriterion if criterion not in missing_set
    }
    return AggregatedCandidateEvaluation(
        candidate_id=candidate_id,
        criterion_scores=criterion_scores,
        composite_score=score,
        confidence=confidence,
        evidence_ids=["signal:official", "pub:prior"],
        risks=risks or [],
        missing_criteria=missing or [],
    )


def _signal(
    signal_id: str = "signal:official",
    *,
    domain: str = "국방인공지능",
    country: str = "대한민국",
) -> TopicSignal:
    return TopicSignal(
        signal_id=signal_id,
        signal_type="external_government_policy",
        title=f"{domain} 공식 정책",
        policy_domains=[domain],
        countries=[country],
        confidence=0.95,
        source_ids=[f"source:{signal_id}"],
        raw_metadata={"external_source": {"reliability_tier": "tier_1_official"}},
    )


def test_weighted_score_uses_configured_seven_criterion_formula() -> None:
    candidate = _candidate("candidate:weighted", "국방 AI 정책평가")

    ranked = rank_candidates(
        [candidate],
        [_aggregate(candidate.candidate_id, 80)],
        [_signal()],
        _config(),
    )

    assert ranked[0].raw_score == 80
    assert ranked[0].penalized_score == 80
    assert ranked[0].penalties == []


def test_penalties_are_named_and_missing_criteria_are_not_hidden() -> None:
    candidate = _candidate(
        "candidate:penalties",
        "전 세계 모든 국가 해외사례 소개",
        signal_id="signal:news",
    )
    aggregate = _aggregate(
        candidate.candidate_id,
        90,
        confidence=0.3,
        risks=["direct_duplicate_detected", "scope_too_broad", "foreign_case_only"],
        missing=[EvaluationCriterion.FEASIBILITY],
    )
    signal = TopicSignal(
        signal_id="signal:news",
        signal_type="external_news_article",
        title="해외사례",
        confidence=0.6,
        source_ids=["source:news"],
        raw_metadata={"external_source": {"reliability_tier": "tier_3_news"}},
    )

    ranked = rank_candidates([candidate], [aggregate], [signal], _config())[0]
    codes = {penalty.code for penalty in ranked.penalties}

    assert {
        "direct_duplicate",
        "official_material_missing",
        "scope_too_broad",
        "foreign_case_only",
        "low_confidence",
        "missing_criterion",
    } <= codes
    assert ranked.penalized_score < ranked.raw_score


def test_ties_are_resolved_by_candidate_id() -> None:
    candidates = [
        _candidate("candidate:b", "후보 B"),
        _candidate("candidate:a", "후보 A"),
    ]
    aggregates = [_aggregate(candidate.candidate_id, 80) for candidate in candidates]

    ranked = rank_candidates(candidates, aggregates, [_signal()], _config())

    assert [topic.candidate.candidate_id for topic in ranked] == [
        "candidate:a",
        "candidate:b",
    ]


def test_diversity_reduces_ai_country_and_output_concentration() -> None:
    candidates = [
        _candidate(f"candidate:ai-{index}", f"국방 AI 후보 {index}") for index in range(1, 4)
    ]
    candidates.append(
        _candidate(
            "candidate:space",
            "우주안보 구조 연구",
            output=RecommendedOutputType.RESEARCH_REPORT,
            signal_id="signal:space",
        )
    )
    aggregates = [
        _aggregate(candidate.candidate_id, score)
        for candidate, score in zip(candidates, (90, 89, 88, 85), strict=True)
    ]
    raw_ranked = rank_candidates(
        candidates,
        aggregates,
        [_signal(), _signal("signal:space", domain="우주안보", country="미국")],
        _config(),
    )

    diversified = diversify_candidates(raw_ranked, _config())

    assert diversified[0].candidate.candidate_id == "candidate:ai-1"
    assert diversified[1].candidate.candidate_id == "candidate:space"
    assert any(
        adjustment.code == "domain_concentration"
        for topic in diversified
        for adjustment in topic.diversity_adjustments
    )


def test_diversity_can_be_disabled_and_limit_can_exceed_available_candidates() -> None:
    config = _config()
    disabled = config.model_copy(
        update={"diversity": config.diversity.model_copy(update={"enabled": False})}
    )
    candidates = [
        _candidate("candidate:b", "후보 B"),
        _candidate("candidate:a", "후보 A"),
    ]
    ranked = rank_candidates(
        candidates,
        [_aggregate(candidate.candidate_id, 80) for candidate in candidates],
        [_signal()],
        disabled,
    )

    diversified = diversify_candidates(ranked, disabled, limit=5)

    assert len(diversified) == 2
    assert [topic.candidate.candidate_id for topic in diversified] == [
        "candidate:a",
        "candidate:b",
    ]
    assert all(not topic.diversity_adjustments for topic in diversified)


def test_ranking_and_artifact_are_reproducible(tmp_path: Path) -> None:
    candidate = _candidate("candidate:stable", "재현 가능한 후보")
    config = _config()
    first = diversify_candidates(
        rank_candidates(
            [candidate],
            [_aggregate(candidate.candidate_id, 77)],
            [_signal()],
            config,
        ),
        config,
    )
    second = diversify_candidates(
        rank_candidates(
            [candidate],
            [_aggregate(candidate.candidate_id, 77)],
            [_signal()],
            config,
        ),
        config,
    )

    first_path = write_ranked_candidates(tmp_path, "stable-run", first, config)
    first_text = first_path.read_text(encoding="utf-8")
    second_path = write_ranked_candidates(tmp_path, "stable-run", second, config)

    assert [topic.model_dump_json() for topic in first] == [
        topic.model_dump_json() for topic in second
    ]
    assert second_path.read_text(encoding="utf-8") == first_text


def test_ranking_rejects_ambiguous_duplicate_ids() -> None:
    candidate = _candidate("candidate:duplicate", "중복 후보")
    aggregate = _aggregate(candidate.candidate_id, 80)
    signal = _signal()
    config = _config()

    with pytest.raises(ValueError, match="candidate_id"):
        rank_candidates([candidate, candidate], [aggregate], [signal], config)
    with pytest.raises(ValueError, match="aggregate candidate_id"):
        rank_candidates([candidate], [aggregate, aggregate], [signal], config)
    with pytest.raises(ValueError, match="signal_id"):
        rank_candidates([candidate], [aggregate], [signal, signal], config)

    ranked = rank_candidates([candidate], [aggregate], [signal], config)
    with pytest.raises(ValueError, match="ranked candidate_id"):
        diversify_candidates([ranked[0], ranked[0]], config)
