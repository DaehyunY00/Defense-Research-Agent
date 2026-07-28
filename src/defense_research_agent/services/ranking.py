"""Pure-Python ranking, penalties, diversity adjustment, and artifact writing."""

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from unicodedata import normalize

from defense_research_agent.domain.evaluation import (
    AggregatedCandidateEvaluation,
    EvaluationCriterion,
)
from defense_research_agent.domain.ranking import (
    CandidateAttributes,
    RankedTopic,
    RankingConfig,
    ResearchHorizon,
    ScoreAdjustment,
)
from defense_research_agent.domain.topic import (
    RecommendedOutputType,
    TopicCandidate,
    TopicSignal,
)
from defense_research_agent.path_safety import ensure_outside_read_only_data

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BROAD_SCOPE_MARKERS = ("모든 국가", "전 세계", "국방정책 전반", "종합적으로 분석")
_COUNTRY_MARKERS = ("대한민국", "한국", "북한", "미국", "중국", "러시아", "일본")


def load_ranking_config(path: Path) -> RankingConfig:
    """Read and validate a UTF-8 local ranking configuration."""
    return RankingConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))


def rank_candidates(
    candidates: Sequence[TopicCandidate],
    aggregates: Sequence[AggregatedCandidateEvaluation],
    signals: Sequence[TopicSignal],
    config: RankingConfig,
) -> list[RankedTopic]:
    """Calculate weighted scores and deterministic penalties without diversity."""
    _require_unique_ids(
        [candidate.candidate_id for candidate in candidates],
        "candidate_id",
    )
    _require_unique_ids(
        [aggregate.candidate_id for aggregate in aggregates],
        "aggregate candidate_id",
    )
    _require_unique_ids(
        [signal.signal_id for signal in signals],
        "signal_id",
    )
    aggregate_by_id = {aggregate.candidate_id: aggregate for aggregate in aggregates}
    signal_by_id = {signal.signal_id: signal for signal in signals}
    ranked: list[RankedTopic] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        aggregate = aggregate_by_id.get(candidate.candidate_id)
        criterion_scores = aggregate.criterion_scores if aggregate is not None else {}
        raw_score = round(
            sum(
                criterion_scores.get(criterion.value, 0.0) * weight
                for criterion, weight in config.weights.as_mapping().items()
            ),
            4,
        )
        evidence_ids = list(
            dict.fromkeys(
                [
                    *candidate.supporting_signal_ids,
                    *candidate.related_publication_ids,
                    *(aggregate.evidence_ids if aggregate is not None else []),
                ]
            )
        )
        candidate_signals = [
            signal_by_id[signal_id]
            for signal_id in candidate.supporting_signal_ids
            if signal_id in signal_by_id
        ]
        penalties = _penalties_for(
            candidate,
            aggregate,
            candidate_signals,
            evidence_ids,
            config,
        )
        penalized_score = round(max(0.0, raw_score - sum(item.amount for item in penalties)), 4)
        attributes = derive_candidate_attributes(candidate, candidate_signals)
        ranked.append(
            RankedTopic(
                candidate=candidate,
                rank=1,
                criterion_scores=criterion_scores,
                raw_score=raw_score,
                penalties=penalties,
                penalized_score=penalized_score,
                adjusted_score=penalized_score,
                confidence=aggregate.confidence if aggregate is not None else None,
                evidence_ids=evidence_ids,
                attributes=attributes,
                explanation=[
                    "원점수는 설정된 일곱 평가 기준의 가중합이다.",
                    *(
                        f"{penalty.code}: -{penalty.amount:g}점 ({penalty.reason})"
                        for penalty in penalties
                    ),
                ],
            )
        )
    ranked.sort(key=lambda item: (-item.penalized_score, item.candidate.candidate_id))
    return [_copy_ranked(item, rank=index) for index, item in enumerate(ranked, start=1)]


def diversify_candidates(
    ranked_topics: Sequence[RankedTopic],
    config: RankingConfig,
    *,
    limit: int | None = None,
) -> list[RankedTopic]:
    """Greedily adjust repeated attributes while preserving every score component."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be zero or greater")
    _require_unique_ids(
        [topic.candidate.candidate_id for topic in ranked_topics],
        "ranked candidate_id",
    )
    requested = len(ranked_topics) if limit is None else min(limit, len(ranked_topics))
    if not config.diversity.enabled:
        ordered = sorted(
            ranked_topics,
            key=lambda item: (-item.penalized_score, item.candidate.candidate_id),
        )
        return [
            _copy_ranked(item, rank=index, adjustments=[], adjusted=item.penalized_score)
            for index, item in enumerate(ordered[:requested], start=1)
        ]

    remaining = list(ranked_topics)
    selected: list[RankedTopic] = []
    domain_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    output_counts: Counter[str] = Counter()
    horizon_counts: Counter[str] = Counter()
    while remaining and len(selected) < requested:
        adjusted_options: list[tuple[float, str, RankedTopic, list[ScoreAdjustment]]] = []
        for topic in remaining:
            adjustments = _diversity_adjustments(
                topic.attributes,
                domain_counts,
                country_counts,
                output_counts,
                horizon_counts,
                config,
            )
            score = round(
                max(0.0, topic.penalized_score - sum(item.amount for item in adjustments)),
                4,
            )
            adjusted_options.append((-score, topic.candidate.candidate_id, topic, adjustments))
        _, _, chosen, adjustments = min(adjusted_options)
        adjusted_score = round(
            max(0.0, chosen.penalized_score - sum(item.amount for item in adjustments)),
            4,
        )
        selected.append(
            _copy_ranked(
                chosen,
                rank=len(selected) + 1,
                adjustments=adjustments,
                adjusted=adjusted_score,
            )
        )
        remaining = [
            topic
            for topic in remaining
            if topic.candidate.candidate_id != chosen.candidate.candidate_id
        ]
        _increment_counts(
            chosen.attributes,
            domain_counts,
            country_counts,
            output_counts,
            horizon_counts,
        )
    return selected


def derive_candidate_attributes(
    candidate: TopicCandidate,
    signals: Sequence[TopicSignal],
) -> CandidateAttributes:
    """Derive only explicit signal attributes plus transparent text markers."""
    policy_domains = list(
        dict.fromkeys(domain for signal in signals for domain in signal.policy_domains)
    )
    countries = list(dict.fromkeys(country for signal in signals for country in signal.countries))
    normalized_text = _normalize_text(f"{candidate.working_title} {candidate.research_question}")
    if not policy_domains:
        if "인공지능" in normalized_text or re.search(r"(?<![a-z])ai(?![a-z])", normalized_text):
            policy_domains.append("국방인공지능")
        if "드론" in normalized_text or "무인" in normalized_text:
            policy_domains.append("무인체계")
    if not countries:
        countries.extend(country for country in _COUNTRY_MARKERS if country in normalized_text)
    horizon = (
        ResearchHorizon.SHORT_TERM
        if candidate.recommended_output
        in {
            RecommendedOutputType.DEFENSE_FORUM,
            RecommendedOutputType.KIDA_BRIEF,
        }
        else ResearchHorizon.STRUCTURAL
        if candidate.recommended_output
        in {
            RecommendedOutputType.DEFENSE_POLICY_RESEARCH,
            RecommendedOutputType.RESEARCH_REPORT,
        }
        else ResearchHorizon.UNKNOWN
    )
    return CandidateAttributes(
        policy_domains=policy_domains,
        countries=countries,
        output_type=candidate.recommended_output,
        research_horizon=horizon,
    )


def write_ranked_candidates(
    artifacts_root: Path,
    run_id: str,
    ranked_topics: Sequence[RankedTopic],
    config: RankingConfig,
) -> Path:
    """Write a deterministic run artifact outside the read-only data tree."""
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a safe path segment")
    run_dir = artifacts_root / "runs" / run_id
    ensure_outside_read_only_data(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "ranked_candidates.json"
    payload = {
        "run_id": run_id,
        "ranking_config": config.model_dump(mode="json"),
        "ranked_candidates": [topic.model_dump(mode="json") for topic in ranked_topics],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _penalties_for(
    candidate: TopicCandidate,
    aggregate: AggregatedCandidateEvaluation | None,
    signals: Sequence[TopicSignal],
    evidence_ids: Sequence[str],
    config: RankingConfig,
) -> list[ScoreAdjustment]:
    risks = set(aggregate.risks if aggregate is not None else ())
    penalties: list[ScoreAdjustment] = []
    if "direct_duplicate_detected" in risks:
        penalties.append(
            _adjustment(
                "direct_duplicate",
                config.penalties.direct_duplicate,
                "기존 연구와 직접 중복",
            )
        )
    if {
        "official_material_missing",
        "insufficient_official_evidence",
    } & risks or _external_signals_without_official_source(signals):
        penalties.append(
            _adjustment(
                "official_material_missing",
                config.penalties.official_material_missing,
                "공식 공개자료 근거가 확인되지 않음",
            )
        )
    candidate_text = _normalize_text(f"{candidate.working_title} {candidate.research_question}")
    if "scope_too_broad" in risks or any(
        marker in candidate_text for marker in _BROAD_SCOPE_MARKERS
    ):
        penalties.append(
            _adjustment(
                "scope_too_broad",
                config.penalties.scope_too_broad,
                "연구질문 범위가 지나치게 넓음",
            )
        )
    if "foreign_case_only" in risks or _is_foreign_case_only(candidate_text):
        penalties.append(
            _adjustment(
                "foreign_case_only",
                config.penalties.foreign_case_only,
                "국내 정책 연결이 없는 단순 해외사례 소개",
            )
        )
    if len(set(evidence_ids)) < config.thresholds.minimum_evidence_ids:
        penalties.append(
            _adjustment(
                "insufficient_evidence_ids",
                config.penalties.insufficient_evidence_ids,
                "근거 ID가 설정 기준보다 적음",
            )
        )
    if (
        aggregate is None
        or aggregate.confidence is None
        or (aggregate.confidence < config.thresholds.low_confidence)
    ):
        penalties.append(
            _adjustment(
                "low_confidence",
                config.penalties.low_confidence,
                "평가 confidence가 설정 기준보다 낮음",
            )
        )
    missing_count = (
        len(aggregate.missing_criteria) if aggregate is not None else len(EvaluationCriterion)
    )
    if missing_count:
        penalties.append(
            _adjustment(
                "missing_criterion",
                config.penalties.missing_criterion * missing_count,
                f"평가 기준 {missing_count}개 누락",
            )
        )
    return [penalty for penalty in penalties if penalty.amount > 0]


def _diversity_adjustments(
    attributes: CandidateAttributes,
    domain_counts: Mapping[str, int],
    country_counts: Mapping[str, int],
    output_counts: Mapping[str, int],
    horizon_counts: Mapping[str, int],
    config: RankingConfig,
) -> list[ScoreAdjustment]:
    adjustments: list[ScoreAdjustment] = []
    primary_domain = attributes.policy_domains[0] if attributes.policy_domains else None
    primary_country = attributes.countries[0] if attributes.countries else None
    output_type = attributes.output_type.value if attributes.output_type is not None else None
    horizon = attributes.research_horizon.value
    if primary_domain and domain_counts.get(primary_domain, 0):
        adjustments.append(
            _adjustment(
                "domain_concentration",
                domain_counts[primary_domain] * config.diversity.domain_repeat_penalty,
                f"상위 후보의 정책 분야 반복: {primary_domain}",
            )
        )
    if primary_country and country_counts.get(primary_country, 0):
        adjustments.append(
            _adjustment(
                "country_concentration",
                country_counts[primary_country] * config.diversity.country_repeat_penalty,
                f"상위 후보의 국가·지역 반복: {primary_country}",
            )
        )
    if output_type and output_counts.get(output_type, 0):
        adjustments.append(
            _adjustment(
                "output_concentration",
                output_counts[output_type] * config.diversity.output_repeat_penalty,
                f"상위 후보의 산출물 유형 반복: {output_type}",
            )
        )
    if horizon != ResearchHorizon.UNKNOWN.value and horizon_counts.get(horizon, 0):
        adjustments.append(
            _adjustment(
                "horizon_concentration",
                horizon_counts[horizon] * config.diversity.horizon_repeat_penalty,
                f"상위 후보의 연구 시계 반복: {horizon}",
            )
        )
    return adjustments


def _increment_counts(
    attributes: CandidateAttributes,
    domain_counts: Counter[str],
    country_counts: Counter[str],
    output_counts: Counter[str],
    horizon_counts: Counter[str],
) -> None:
    if attributes.policy_domains:
        domain_counts[attributes.policy_domains[0]] += 1
    if attributes.countries:
        country_counts[attributes.countries[0]] += 1
    if attributes.output_type is not None:
        output_counts[attributes.output_type.value] += 1
    if attributes.research_horizon is not ResearchHorizon.UNKNOWN:
        horizon_counts[attributes.research_horizon.value] += 1


def _copy_ranked(
    topic: RankedTopic,
    *,
    rank: int,
    adjustments: Sequence[ScoreAdjustment] | None = None,
    adjusted: float | None = None,
) -> RankedTopic:
    diversity_adjustments = (
        list(topic.diversity_adjustments) if adjustments is None else list(adjustments)
    )
    adjusted_score = topic.adjusted_score if adjusted is None else adjusted
    explanation = [
        *topic.explanation,
        *(f"{item.code}: -{item.amount:g}점 ({item.reason})" for item in diversity_adjustments),
    ]
    return RankedTopic(
        candidate=topic.candidate,
        rank=rank,
        criterion_scores=topic.criterion_scores,
        raw_score=topic.raw_score,
        penalties=topic.penalties,
        penalized_score=topic.penalized_score,
        diversity_adjustments=diversity_adjustments,
        adjusted_score=adjusted_score,
        confidence=topic.confidence,
        evidence_ids=topic.evidence_ids,
        attributes=topic.attributes,
        explanation=explanation,
    )


def _adjustment(code: str, amount: float, reason: str) -> ScoreAdjustment:
    return ScoreAdjustment(code=code, amount=round(amount, 4), reason=reason)


def _external_signals_without_official_source(signals: Sequence[TopicSignal]) -> bool:
    external = [
        signal
        for signal in signals
        if signal.signal_type.startswith("external_") or signal.source_ids
    ]
    if not external:
        return False
    return not any(
        isinstance(source_metadata, dict)
        and source_metadata.get("reliability_tier") == "tier_1_official"
        for signal in external
        for source_metadata in [signal.raw_metadata.get("external_source")]
    )


def _is_foreign_case_only(candidate_text: str) -> bool:
    has_foreign_case = "해외사례" in candidate_text or "사례소개" in candidate_text
    has_policy_link = any(
        marker in candidate_text for marker in ("한국", "대한민국", "국방정책", "정책함의", "적용")
    )
    return has_foreign_case and not has_policy_link


def _normalize_text(value: str) -> str:
    return " ".join(normalize("NFC", value).casefold().split())


def _require_unique_ids(values: Sequence[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} values must be unique")
