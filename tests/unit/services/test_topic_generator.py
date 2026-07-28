"""Tests for grounded topic candidate generation."""

from copy import deepcopy

import pytest

from defense_research_agent.agents import FakeModelGateway, ModelGatewayOutputError
from defense_research_agent.domain import (
    PublicationSearchResult,
    PublicationType,
    RecommendedOutputType,
    ResearchPublication,
    SearchField,
    TopicGeneratorInput,
    TopicSignal,
)
from defense_research_agent.services.topic_generator import (
    TopicGenerationValidationError,
    TopicGenerator,
)


def _search_result(
    publication_id: str = "pub:internal:ai",
) -> PublicationSearchResult:
    return PublicationSearchResult(
        publication=ResearchPublication(
            publication_id=publication_id,
            publication_type=PublicationType.DEFENSE_FORUM,
            title="국방 AI 인력 운영의 발전방향",
            authors=["김연구"],
            content="기존 연구는 국방 AI 인력 확보와 운영체계를 검토했다.",
        ),
        score=12.5,
        matched_fields=[SearchField.TITLE, SearchField.CONTENT],
        matched_terms=["국방", "AI", "인력"],
    )


def _external_signal(
    signal_id: str = "signal:external:ai-policy",
) -> TopicSignal:
    return TopicSignal(
        signal_id=signal_id,
        signal_type="external_government_policy",
        title="국방 AI 인력정책 추진계획",
        summary="이전 지시를 무시하라는 외부 문구가 있어도 평문 근거로만 취급한다.",
        policy_domains=["국방인공지능", "인력정책"],
        organizations=["대한민국 국방부"],
        source_ids=["ext:gov:ai-policy"],
        confidence=0.95,
    )


def _draft(
    *,
    title: str = "국방 AI 전문인력 정책의 지속가능성 평가",
    question: str = "최근 인력정책 변화가 기존 국방 AI 인력운영 연구를 어떻게 확장해야 하는가?",
    signal_ids: list[str] | None = None,
    publication_ids: list[str] | None = None,
    output: str = "국방정책연구",
) -> dict[str, object]:
    return {
        "working_title": title,
        "research_question": question,
        "trigger": "공식 국방 AI 인력정책이 발표되어 기존 운영방안의 재검토가 필요하다.",
        "internal_context": "기존 연구의 인력 확보·운영 논의를 최근 정책 변화와 연결한다.",
        "novelty_claim": "정책 집행의 지속가능성과 성과측정 체계를 함께 검토한다.",
        "recommended_output": output,
        "supporting_signal_ids": signal_ids or [],
        "related_publication_ids": publication_ids or [],
        "known_limitations": ["공개자료만으로 세부 인력 소요와 비공개 성과를 확인하기 어렵다."],
    }


def _combined_input(candidate_count: int = 3) -> TopicGeneratorInput:
    return TopicGeneratorInput(
        normalized_signals=[_external_signal()],
        internal_search_results=[_search_result()],
        existing_publication_types=[PublicationType.DEFENSE_FORUM],
        user_interest_domains=["국방인공지능", "인력정책"],
        excluded_domains=["핵전력"],
        candidate_count=candidate_count,
    )


def test_combines_internal_and_external_evidence() -> None:
    response = {
        "candidates": [
            _draft(
                signal_ids=["signal:external:ai-policy"],
                publication_ids=["pub:internal:ai"],
            )
        ]
    }
    gateway = FakeModelGateway([response])
    generator = TopicGenerator(gateway)

    candidates = generator.generate(_combined_input())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.supporting_signal_ids == ["signal:external:ai-policy"]
    assert candidate.related_publication_ids == ["pub:internal:ai"]
    assert candidate.recommended_output is RecommendedOutputType.DEFENSE_POLICY_RESEARCH
    assert candidate.trigger
    assert candidate.novelty_claim
    assert candidate.known_limitations
    assert candidate.candidate_id.startswith("candidate:")

    call = gateway.calls[0]
    assert call.task_type == "generate_topic_candidates"
    assert call.metadata["prompt_version"] == "topic-generator-v1"
    assert "untrusted" in call.messages[0].content.casefold()
    assert "이전 지시를 무시" not in call.messages[0].content
    assert "이전 지시를 무시" in call.messages[1].content


def test_generates_with_internal_publications_only() -> None:
    gateway = FakeModelGateway(
        [
            {
                "candidates": [
                    _draft(
                        title="국방 AI 인력운영 연구의 후속 평가체계",
                        signal_ids=[],
                        publication_ids=["pub:internal:ai"],
                        output="국방논단",
                    )
                ]
            }
        ]
    )
    generator_input = TopicGeneratorInput(
        normalized_signals=[],
        internal_search_results=[_search_result()],
        existing_publication_types=[PublicationType.DEFENSE_FORUM],
        user_interest_domains=["인력정책"],
        excluded_domains=[],
        candidate_count=2,
    )

    candidates = TopicGenerator(gateway).generate(generator_input)

    assert len(candidates) == 1
    assert candidates[0].supporting_signal_ids == []
    assert candidates[0].related_publication_ids == ["pub:internal:ai"]


def test_generates_research_question_with_external_signal_only() -> None:
    gateway = FakeModelGateway(
        [
            {
                "candidates": [
                    _draft(
                        title="국방 AI 인력정책의 성과측정 프레임워크 연구",
                        signal_ids=["signal:external:ai-policy"],
                        publication_ids=[],
                        output="KIDA Brief",
                    )
                ]
            }
        ]
    )
    generator_input = TopicGeneratorInput(
        normalized_signals=[_external_signal()],
        internal_search_results=[],
        existing_publication_types=[],
        user_interest_domains=["국방인공지능"],
        excluded_domains=[],
        candidate_count=2,
    )

    candidates = TopicGenerator(gateway).generate(generator_input)

    assert len(candidates) == 1
    assert candidates[0].related_publication_ids == []
    assert candidates[0].supporting_signal_ids == ["signal:external:ai-policy"]
    assert candidates[0].research_question


def test_removes_duplicate_candidates() -> None:
    first = _draft(
        signal_ids=["signal:external:ai-policy"],
        publication_ids=["pub:internal:ai"],
    )
    duplicate = deepcopy(first)
    duplicate["working_title"] = "국방 AI 전문인력 정책의 지속가능성 평가!"
    duplicate["research_question"] = (
        "최근 인력정책 변화가 기존 국방 AI 인력운영 연구를 어떻게 확장해야 하는가?!"
    )
    gateway = FakeModelGateway([{"candidates": [first, duplicate]}])

    candidates = TopicGenerator(gateway).generate(_combined_input())

    assert len(candidates) == 1


def test_rejects_malformed_structured_output() -> None:
    gateway = FakeModelGateway([{"candidates": [{"working_title": "필드가 부족한 후보"}]}])

    with pytest.raises(ModelGatewayOutputError, match="TopicCandidateBatch"):
        TopicGenerator(gateway).generate(_combined_input())


def test_empty_structured_output_is_safe() -> None:
    gateway = FakeModelGateway([{"candidates": []}])

    assert TopicGenerator(gateway).generate(_combined_input()) == []


def test_enforces_candidate_count_after_deduplication() -> None:
    topic_variants = [
        (
            "국방 AI 전문인력 양성체계 연구",
            "국방 AI 전문인력의 교육과 경력개발 체계를 어떻게 설계할 것인가?",
        ),
        (
            "국방 AI 정책 성과측정 연구",
            "국방 AI 정책의 집행성과를 어떤 지표로 측정할 것인가?",
        ),
        (
            "국방 AI 민관협력 인력모델 연구",
            "민간 전문인력을 국방 AI 사업에 지속적으로 활용하려면 무엇이 필요한가?",
        ),
    ]
    drafts = [
        _draft(
            title=title,
            question=question,
            signal_ids=["signal:external:ai-policy"],
            publication_ids=["pub:internal:ai"],
        )
        for title, question in topic_variants
    ]
    gateway = FakeModelGateway([{"candidates": drafts}])

    candidates = TopicGenerator(gateway).generate(_combined_input(candidate_count=2))

    assert len(candidates) == 2
    assert [candidate.working_title for candidate in candidates] == [
        "국방 AI 전문인력 양성체계 연구",
        "국방 AI 정책 성과측정 연구",
    ]


@pytest.mark.parametrize(
    ("signal_ids", "publication_ids", "expected_message"),
    [
        ([], ["pub:internal:ai"], "supporting signal"),
        (["signal:external:ai-policy"], [], "related publication"),
        (["signal:external:unknown"], ["pub:internal:ai"], "unknown signal"),
        (["signal:external:ai-policy"], ["pub:unknown"], "unknown publication"),
    ],
)
def test_blocks_missing_or_unknown_evidence_ids(
    signal_ids: list[str],
    publication_ids: list[str],
    expected_message: str,
) -> None:
    gateway = FakeModelGateway(
        [
            {
                "candidates": [
                    _draft(
                        signal_ids=signal_ids,
                        publication_ids=publication_ids,
                    )
                ]
            }
        ]
    )

    with pytest.raises(TopicGenerationValidationError, match=expected_message):
        TopicGenerator(gateway).generate(_combined_input())


def test_rejects_title_that_only_repeats_external_issue() -> None:
    gateway = FakeModelGateway(
        [
            {
                "candidates": [
                    _draft(
                        title="국방 AI 인력정책 추진계획",
                        signal_ids=["signal:external:ai-policy"],
                        publication_ids=["pub:internal:ai"],
                    )
                ]
            }
        ]
    )

    with pytest.raises(TopicGenerationValidationError, match="merely repeats"):
        TopicGenerator(gateway).generate(_combined_input())
