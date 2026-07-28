"""Unit tests for research-publication and topic domain models."""

from datetime import date, datetime

import pytest
from pydantic import BaseModel, HttpUrl, ValidationError

from defense_research_agent.domain import (
    EvaluationCriterion,
    EvaluationResult,
    JsonObject,
    PublicationChunk,
    PublicationType,
    RecommendedOutputType,
    ResearchPublication,
    TopicCandidate,
    TopicSignal,
)


def _publication() -> ResearchPublication:
    raw_metadata: JsonObject = {
        "filename": "2024_홍길동_국방AI정책연구.pdf",
        "category": "국방논단",
        "num_pages": 12,
        "source": {"provider": "KIDA", "verified": True},
    }
    return ResearchPublication(
        publication_id="pub:kida:2024-ai",
        publication_type=PublicationType.DEFENSE_FORUM,
        title="신뢰할 수 있는 국방 AI 구현 방안",
        subtitle="정책과 제도를 중심으로",
        authors=["홍길동", "김국방"],
        organization="한국국방연구원",
        publication_date=date(2024, 11, 4),
        issue_number="제2015호",
        volume="24-42",
        abstract="국방 AI의 신뢰성과 책임성을 검토한다.",
        keywords=["국방 AI", "신뢰성"],
        language="ko",
        source_url=HttpUrl("https://www.kida.re.kr/example"),
        local_path="data/국방논단/2024_홍길동_국방AI정책연구.pdf",
        raw_metadata=raw_metadata,
        content="한글 본문은 원문 그대로 보존한다.\n두 번째 줄.",
        created_at=datetime(2026, 2, 2, 23, 30, 0),
        checksum="a" * 64,
    )


def _models() -> list[BaseModel]:
    publication = _publication()
    chunk = PublicationChunk(
        chunk_id="chunk:2024-ai:1",
        publication_id=publication.publication_id,
        section="서론",
        page=1,
        sequence=0,
        text="  한글 청크의 앞뒤 공백도 보존한다.  ",
        token_count=12,
        metadata={"char_count": 21},
    )
    signal = TopicSignal(
        signal_id="signal:ai-policy",
        signal_type="policy_change",
        title="국방 AI 책임성 논의 확대",
        summary="국방 AI의 검증과 책임 구조가 정책 의제로 부상했다.",
        event_date=date(2026, 7, 1),
        publication_ids=[publication.publication_id],
        policy_domains=["국방정보화"],
        countries=["대한민국"],
        organizations=["국방부"],
        keywords=["AI", "책임성"],
        confidence=0.85,
    )
    candidate = TopicCandidate(
        candidate_id="candidate:ai-assurance",
        working_title="국방 AI 보증체계 발전방안",
        research_question="국방 AI의 신뢰성을 어떤 제도와 평가체계로 보증할 것인가?",
        trigger="국방 AI 도입 확대",
        internal_context="기존 KIDA 연구의 국방 AI 정책 논의를 계승한다.",
        novelty_claim="획득 이후의 지속 검증과 책임 구조를 함께 다룬다.",
        recommended_output=RecommendedOutputType.DEFENSE_POLICY_RESEARCH,
        supporting_signal_ids=[signal.signal_id],
        related_publication_ids=[publication.publication_id],
        known_limitations=["실증 데이터의 공개 범위가 제한적이다."],
    )
    evaluation = EvaluationResult(
        candidate_id=candidate.candidate_id,
        criterion=EvaluationCriterion.POLICY_RELEVANCE,
        score=92.5,
        rationale="현재 국방 AI 도입 정책과 직접 연결된다.",
        evidence_ids=[signal.signal_id, publication.publication_id],
        risks=["최신 비공개 사업 정보는 평가할 수 없다."],
        confidence=0.8,
    )
    return [publication, chunk, signal, candidate, evaluation]


def test_models_accept_normal_data() -> None:
    publication, chunk, signal, candidate, evaluation = _models()

    assert isinstance(publication, ResearchPublication)
    assert isinstance(chunk, PublicationChunk)
    assert isinstance(signal, TopicSignal)
    assert isinstance(candidate, TopicCandidate)
    assert isinstance(evaluation, EvaluationResult)
    assert publication.raw_metadata["category"] == "국방논단"
    assert chunk.text.startswith("  한글")
    assert evaluation.score == 92.5


def test_missing_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="publication_id"):
        ResearchPublication.model_validate({"publication_type": "kida_brief"})


def test_invalid_entity_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="publication_id"):
        ResearchPublication.model_validate(
            {
                "publication_id": "공백 포함 ID",
                "publication_type": "kida_brief",
            }
        )


def test_invalid_date_is_rejected() -> None:
    with pytest.raises(ValidationError, match="publication_date"):
        ResearchPublication.model_validate(
            {
                "publication_id": "pub:invalid-date",
                "publication_type": "research_report",
                "publication_date": "2024-02-30",
            }
        )


@pytest.mark.parametrize("score", [-0.01, 100.01])
def test_invalid_score_is_rejected(score: float) -> None:
    with pytest.raises(ValidationError, match="score"):
        EvaluationResult.model_validate(
            {
                "candidate_id": "candidate:test",
                "criterion": "신규성",
                "score": score,
                "rationale": "범위를 벗어난 점수",
                "confidence": 0.5,
            }
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_uses_consistent_zero_to_one_range(confidence: float) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        TopicSignal.model_validate(
            {
                "signal_id": "signal:test",
                "signal_type": "test",
                "title": "테스트 신호",
                "confidence": confidence,
            }
        )


def test_unknown_publication_type_is_rejected() -> None:
    with pytest.raises(ValidationError, match="publication_type"):
        ResearchPublication.model_validate(
            {
                "publication_id": "pub:unknown-type",
                "publication_type": "unknown_newsletter",
            }
        )


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("국방논단", PublicationType.DEFENSE_FORUM),
        ("Brief", PublicationType.KIDA_BRIEF),
        ("국방정책연구", PublicationType.DEFENSE_POLICY_RESEARCH),
        ("연구보고서", PublicationType.RESEARCH_REPORT),
        ("security_strategy_focus", PublicationType.SECURITY_STRATEGY_FOCUS),
        ("other", PublicationType.OTHER),
    ],
)
def test_publication_type_accepts_canonical_and_dataset_values(
    source_value: str,
    expected: PublicationType,
) -> None:
    publication = ResearchPublication.model_validate(
        {
            "publication_id": "pub:type-test",
            "publication_type": source_value,
        }
    )

    assert publication.publication_type is expected


def test_models_json_round_trip_without_damaging_korean() -> None:
    for model in _models():
        encoded = model.model_dump_json()
        restored = type(model).model_validate_json(encoded)

        assert restored == model
        assert "\\ud55c\\uae00" not in encoded

    publication_json = _publication().model_dump_json()
    assert "한글 본문" in publication_json
