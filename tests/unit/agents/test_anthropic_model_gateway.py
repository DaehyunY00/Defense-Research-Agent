"""Contract tests for the Claude structured-output gateway."""

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from defense_research_agent.agents import (
    AnthropicModelGateway,
    AnthropicResearchLabAgents,
    AnthropicRuntimeSettings,
    ModelGatewayOutputError,
    ModelGatewayProviderError,
    ModelGatewayRefusalError,
    ModelMessage,
    ModelMessageRole,
    build_anthropic_research_lab_agents,
    build_anthropic_role_gateways,
    default_anthropic_role_model_ids,
)
from defense_research_agent.domain import (
    ModelProvider,
    ModelRoute,
    ResearchRole,
    TopicCandidateBatch,
)


@dataclass(slots=True)
class _FakeUsage:
    input_tokens: int = 120
    output_tokens: int = 40


@dataclass(slots=True)
class _FakeResponse:
    parsed_output: object | None
    stop_reason: str = "end_turn"
    usage: _FakeUsage = field(default_factory=_FakeUsage)
    _request_id: str = "req_test"


class _FakeMessages:
    def __init__(
        self,
        response: object | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self._error is not None:
            raise self._error
        if self._response is None:
            raise RuntimeError("fake response is not configured")
        return self._response


@dataclass(slots=True)
class _FakeClient:
    messages: _FakeMessages


def _route(
    *,
    model_id: str = "claude-opus-5",
    max_output_tokens: int = 2_048,
) -> ModelRoute:
    return ModelRoute(
        provider=ModelProvider.ANTHROPIC,
        model_id=model_id,
        max_output_tokens=max_output_tokens,
    )


def test_settings_require_only_key_and_mask_it() -> None:
    settings = AnthropicRuntimeSettings.from_environment({"ANTHROPIC_API_KEY": "sk-ant-secret"})

    assert settings.api_key.get_secret_value() == "sk-ant-secret"
    assert settings.timeout_seconds == 90.0
    assert settings.max_retries == 2
    assert settings.role_model_ids == default_anthropic_role_model_ids()
    assert "sk-ant-secret" not in repr(settings)
    assert all(
        route.provider is ModelProvider.ANTHROPIC for route in settings.model_routes().values()
    )


def test_settings_accept_operational_and_role_overrides() -> None:
    settings = AnthropicRuntimeSettings.from_environment(
        {
            "ANTHROPIC_API_KEY": "test-key",
            "DEFENSE_RESEARCH_CLAUDE_TIMEOUT_SECONDS": "45.5",
            "DEFENSE_RESEARCH_CLAUDE_MAX_RETRIES": "1",
            "DEFENSE_RESEARCH_CLAUDE_MODEL_DEVELOPER_RESEARCHER": "custom-code-model",
        }
    )

    assert settings.timeout_seconds == 45.5
    assert settings.max_retries == 1
    assert settings.role_model_ids[ResearchRole.DEVELOPER_RESEARCHER] == "custom-code-model"


def test_settings_reject_missing_or_invalid_environment_values() -> None:
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        AnthropicRuntimeSettings.from_environment({})

    with pytest.raises(ValueError, match="must be an integer"):
        AnthropicRuntimeSettings.from_environment(
            {
                "ANTHROPIC_API_KEY": "test-key",
                "DEFENSE_RESEARCH_CLAUDE_MAX_RETRIES": "many",
            }
        )


def test_gateway_uses_sdk_pydantic_parse_and_records_secret_free_audit() -> None:
    messages_client = _FakeMessages(_FakeResponse(parsed_output=TopicCandidateBatch(candidates=[])))
    gateway = AnthropicModelGateway(_route(), _FakeClient(messages_client))

    output = gateway.generate_structured(
        task_type="generate_topic_candidates",
        messages=[
            ModelMessage(role=ModelMessageRole.SYSTEM, content="system boundary"),
            ModelMessage(role=ModelMessageRole.USER, content="private research question"),
        ],
        output_schema=TopicCandidateBatch,
        metadata={
            "prompt_version": "test-v1",
            "project_id": "project:test",
            "untrusted_extra": "must-not-be-audited",
        },
    )

    assert output.candidates == []
    call = messages_client.calls[0]
    assert call == {
        "model": "claude-opus-5",
        "max_tokens": 2_048,
        "messages": [{"role": "user", "content": "private research question"}],
        "output_format": TopicCandidateBatch,
        "system": "system boundary",
    }
    audit = gateway.audit_records[0]
    assert audit.status == "success"
    assert audit.input_tokens == 120
    assert audit.output_tokens == 40
    assert audit.request_id == "req_test"
    assert audit.metadata == {
        "prompt_version": "test-v1",
        "project_id": "project:test",
    }
    assert "private research question" not in repr(audit)


@pytest.mark.parametrize(
    "response, error_type, message",
    [
        (
            _FakeResponse(parsed_output=None, stop_reason="refusal"),
            ModelGatewayRefusalError,
            "refused",
        ),
        (
            _FakeResponse(parsed_output=None, stop_reason="max_tokens"),
            ModelGatewayOutputError,
            "max_tokens",
        ),
        (
            _FakeResponse(parsed_output={"unexpected": True}),
            ModelGatewayOutputError,
            "TopicCandidateBatch",
        ),
    ],
)
def test_gateway_classifies_refusal_truncation_and_invalid_output(
    response: _FakeResponse,
    error_type: type[Exception],
    message: str,
) -> None:
    gateway = AnthropicModelGateway(
        _route(),
        _FakeClient(_FakeMessages(response)),
    )

    with pytest.raises(error_type, match=message):
        gateway.generate_structured(
            "test-task",
            [ModelMessage(role=ModelMessageRole.USER, content="request")],
            TopicCandidateBatch,
            {},
        )

    assert gateway.audit_records[0].status in {"refusal", "output_error"}


def test_gateway_sanitizes_provider_exception_and_rejects_empty_user_messages() -> None:
    gateway = AnthropicModelGateway(
        _route(),
        _FakeClient(_FakeMessages(error=RuntimeError("sk-ant-secret provider detail"))),
    )

    with pytest.raises(ModelGatewayProviderError) as captured:
        gateway.generate_structured(
            "test-task",
            [ModelMessage(role=ModelMessageRole.USER, content="request")],
            TopicCandidateBatch,
            {},
        )

    assert "sk-ant-secret" not in str(captured.value)
    assert gateway.audit_records[0].status == "provider_error"
    with pytest.raises(ModelGatewayProviderError, match="user message"):
        gateway.generate_structured(
            "test-task",
            [ModelMessage(role=ModelMessageRole.SYSTEM, content="system only")],
            TopicCandidateBatch,
            {},
        )


def test_factory_builds_seven_role_bound_gateways_with_one_client() -> None:
    settings = AnthropicRuntimeSettings.from_environment({"ANTHROPIC_API_KEY": "test-key"})
    client = _FakeClient(
        _FakeMessages(_FakeResponse(parsed_output=TopicCandidateBatch(candidates=[])))
    )

    gateways = build_anthropic_role_gateways(settings, client=client)

    assert set(gateways) == set(ResearchRole)
    assert gateways[ResearchRole.MAIN_RESEARCHER].route.model_id == "claude-opus-5"
    assert gateways[ResearchRole.LITERATURE_RESEARCHER].route.model_id == "claude-haiku-4-5"
    assert gateways[ResearchRole.DEVELOPER_RESEARCHER].route.model_id == "claude-sonnet-5"


def test_research_lab_factory_routes_pi_and_all_six_workers() -> None:
    settings = AnthropicRuntimeSettings.from_environment({"ANTHROPIC_API_KEY": "test-key"})
    client = _FakeClient(
        _FakeMessages(_FakeResponse(parsed_output=TopicCandidateBatch(candidates=[])))
    )

    runtime = build_anthropic_research_lab_agents(settings, client=client)

    assert isinstance(runtime, AnthropicResearchLabAgents)
    assert runtime.main_researcher.spec.role is ResearchRole.MAIN_RESEARCHER
    assert set(runtime.workers) == set(ResearchRole) - {ResearchRole.MAIN_RESEARCHER}
    assert (
        runtime.workers[ResearchRole.LITERATURE_RESEARCHER].spec.model_route.model_id
        == "claude-haiku-4-5"
    )
    assert (
        runtime.workers[ResearchRole.METHODOLOGY_RESEARCHER].spec.model_route.model_id
        == "claude-sonnet-5"
    )
    assert (
        runtime.workers[ResearchRole.CRITICAL_REVIEWER].spec.model_route.model_id == "claude-opus-5"
    )
