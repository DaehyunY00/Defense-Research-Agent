"""End-to-end LangGraph tests using only the fake model gateway."""

from defense_research_agent.agents import FakeModelGateway
from defense_research_agent.domain import (
    PublicationSearchResult,
    PublicationType,
    ResearchPublication,
    SearchField,
    TopicSignal,
)
from defense_research_agent.graph import (
    TopicGenerationState,
    build_topic_generation_graph,
    generate_topic_candidates,
)
from defense_research_agent.services.topic_generator import TopicGenerator


def _state() -> TopicGenerationState:
    signal = TopicSignal(
        signal_id="signal:external:graph",
        signal_type="external_government_policy",
        title="국방 AI 정책 시행계획",
        summary="공식 정책 시행계획이 공개됐다.",
        source_ids=["ext:gov:graph"],
        confidence=0.95,
    )
    publication = ResearchPublication(
        publication_id="pub:graph:internal",
        publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
        title="국방 AI 정책 기반 연구",
        content="기존 정책 연구의 범위와 한계를 분석한다.",
    )
    search_result = PublicationSearchResult(
        publication=publication,
        score=10.0,
        matched_fields=[SearchField.TITLE],
        matched_terms=["국방", "AI"],
    )
    return {
        "normalized_signals": [signal],
        "internal_search_results": [search_result],
        "existing_publication_types": [PublicationType.DEFENSE_POLICY_RESEARCH],
        "user_interest_domains": ["국방인공지능"],
        "excluded_domains": [],
        "candidate_count": 1,
    }


def _response() -> dict[str, object]:
    return {
        "candidates": [
            {
                "working_title": "국방 AI 정책 집행의 성과검증 체계",
                "research_question": (
                    "기존 정책 연구를 바탕으로 최근 시행계획의 성과를 어떻게 검증할 것인가?"
                ),
                "trigger": "공식 시행계획 공개로 정책 집행 검증 수요가 생겼다.",
                "internal_context": "기존 국방 AI 정책 연구의 범위와 한계를 출발점으로 삼는다.",
                "novelty_claim": "집행 단계의 측정지표와 환류 구조를 새롭게 제시한다.",
                "recommended_output": "국방정책연구",
                "supporting_signal_ids": ["signal:external:graph"],
                "related_publication_ids": ["pub:graph:internal"],
                "known_limitations": ["비공개 사업 성과자료는 공개자료만으로 검증하기 어렵다."],
            }
        ]
    }


def test_fake_gateway_end_to_end_topic_generation_graph() -> None:
    gateway = FakeModelGateway([_response()])
    graph = build_topic_generation_graph(TopicGenerator(gateway))

    result = graph.invoke(_state())
    candidates = result.get("topic_candidates")

    assert candidates is not None
    assert len(candidates) == 1
    assert candidates[0].supporting_signal_ids == ["signal:external:graph"]
    assert candidates[0].related_publication_ids == ["pub:graph:internal"]
    assert "generate_topic_candidates" in graph.get_graph().nodes
    assert len(gateway.calls) == 1


def test_generate_node_handles_no_evidence_without_calling_model() -> None:
    gateway = FakeModelGateway([])
    state = _state()
    state["normalized_signals"] = []
    state["internal_search_results"] = []

    update = generate_topic_candidates(state, TopicGenerator(gateway))

    assert update == {"topic_candidates": []}
    assert gateway.calls == []
