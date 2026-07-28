"""Deterministic structured-output fake for offline tests."""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from defense_research_agent.agents.model_gateway import (
    ModelGateway,
    ModelGatewayExhaustedError,
    ModelGatewayOutputError,
    ModelMessage,
    StructuredOutputT,
)
from defense_research_agent.domain import JsonObject


@dataclass(frozen=True, slots=True)
class FakeModelCall:
    """Captured fake call for prompt-boundary assertions."""

    task_type: str
    messages: tuple[ModelMessage, ...]
    output_schema_name: str
    metadata: JsonObject


class FakeModelGateway(ModelGateway):
    """Return queued values after validating them with the requested schema."""

    def __init__(self, responses: Sequence[object]) -> None:
        self._responses = list(responses)
        self.calls: list[FakeModelCall] = []

    def generate_structured(
        self,
        task_type: str,
        messages: Sequence[ModelMessage],
        output_schema: type[StructuredOutputT],
        metadata: JsonObject,
    ) -> StructuredOutputT:
        """Validate the next queued response exactly like a real gateway boundary."""
        self.calls.append(
            FakeModelCall(
                task_type=task_type,
                messages=tuple(messages),
                output_schema_name=output_schema.__name__,
                metadata=dict(metadata),
            )
        )
        if not self._responses:
            raise ModelGatewayExhaustedError("fake model response queue is empty")

        response = self._responses.pop(0)
        try:
            if isinstance(response, str):
                return output_schema.model_validate_json(response)
            if isinstance(response, BaseModel):
                return output_schema.model_validate(response.model_dump())
            return output_schema.model_validate(response)
        except (ValidationError, ValueError) as error:
            raise ModelGatewayOutputError(
                f"structured output failed {output_schema.__name__} validation"
            ) from error
