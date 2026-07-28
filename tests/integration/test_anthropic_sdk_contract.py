"""Offline contract test against the installed official Anthropic SDK."""

import json
from typing import cast

import httpx
from anthropic import Anthropic

from defense_research_agent.agents import (
    AnthropicModelGateway,
    ModelMessage,
    ModelMessageRole,
)
from defense_research_agent.agents.anthropic_model_gateway import AnthropicClient
from defense_research_agent.domain import (
    ModelProvider,
    ModelRoute,
    TopicCandidateBatch,
)


def test_official_sdk_translates_pydantic_to_output_config_without_network() -> None:
    captured_body: dict[str, object] = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_body.update(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "msg_contract",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": '{"candidates":[]}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 5},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handle_request))
    sdk_client = Anthropic(
        api_key="sk-ant-contract-test",
        http_client=http_client,
        max_retries=0,
    )
    gateway = AnthropicModelGateway(
        ModelRoute(
            provider=ModelProvider.ANTHROPIC,
            model_id="claude-opus-5",
            max_output_tokens=4_096,
        ),
        cast(AnthropicClient, sdk_client),
    )

    output = gateway.generate_structured(
        "contract-test",
        [
            ModelMessage(role=ModelMessageRole.SYSTEM, content="Return the schema."),
            ModelMessage(role=ModelMessageRole.USER, content="Return an empty candidate list."),
        ],
        TopicCandidateBatch,
        {"prompt_version": "contract-v1"},
    )

    assert output.candidates == []
    assert captured_body["model"] == "claude-opus-5"
    assert captured_body["max_tokens"] == 4_096
    assert captured_body["system"] == "Return the schema."
    assert captured_body["messages"] == [
        {"role": "user", "content": "Return an empty candidate list."}
    ]
    assert "temperature" not in captured_body
    output_config = cast(dict[str, object], captured_body["output_config"])
    output_format = cast(dict[str, object], output_config["format"])
    assert output_format["type"] == "json_schema"
    schema = cast(dict[str, object], output_format["schema"])
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert gateway.audit_records[0].input_tokens == 12
    assert gateway.audit_records[0].output_tokens == 5
