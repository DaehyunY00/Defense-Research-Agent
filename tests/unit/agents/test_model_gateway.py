"""Tests for the deterministic structured-output model fake."""

import pytest

from defense_research_agent.agents import (
    FakeModelGateway,
    ModelGateway,
    ModelGatewayExhaustedError,
    ModelGatewayOutputError,
    ModelMessage,
    ModelMessageRole,
)
from defense_research_agent.domain import TopicCandidateBatch


def test_fake_gateway_implements_interface_and_validates_response() -> None:
    gateway = FakeModelGateway([{"candidates": []}])

    output = gateway.generate_structured(
        task_type="generate_topic_candidates",
        messages=[ModelMessage(role=ModelMessageRole.USER, content="fixture")],
        output_schema=TopicCandidateBatch,
        metadata={"prompt_version": "test"},
    )

    assert isinstance(gateway, ModelGateway)
    assert output.candidates == []
    assert gateway.calls[0].output_schema_name == "TopicCandidateBatch"


def test_fake_gateway_rejects_invalid_json_and_empty_queue() -> None:
    invalid_gateway = FakeModelGateway(["not-json"])
    with pytest.raises(ModelGatewayOutputError, match="TopicCandidateBatch"):
        invalid_gateway.generate_structured(
            "generate_topic_candidates",
            [],
            TopicCandidateBatch,
            {},
        )

    empty_gateway = FakeModelGateway([])
    with pytest.raises(ModelGatewayExhaustedError, match="queue"):
        empty_gateway.generate_structured(
            "generate_topic_candidates",
            [],
            TopicCandidateBatch,
            {},
        )
