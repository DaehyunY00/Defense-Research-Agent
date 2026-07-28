"""Framework-neutral structured model gateway contract."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel

from defense_research_agent.domain import DomainModel, JsonObject

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


class ModelMessageRole(StrEnum):
    """Minimal model message roles used by application services."""

    SYSTEM = "system"
    USER = "user"


class ModelMessage(DomainModel):
    """A model-facing message with content kept separate by role."""

    role: ModelMessageRole
    content: str


class ModelGateway(ABC):
    """Replaceable gateway whose outputs must validate against Pydantic."""

    @abstractmethod
    def generate_structured(
        self,
        task_type: str,
        messages: Sequence[ModelMessage],
        output_schema: type[StructuredOutputT],
        metadata: JsonObject,
    ) -> StructuredOutputT:
        """Generate and validate one structured model response."""


class ModelGatewayError(RuntimeError):
    """Base class for expected structured generation failures."""


class ModelGatewayOutputError(ModelGatewayError):
    """Raised when a provider response does not match the requested schema."""


class ModelGatewayProviderError(ModelGatewayError):
    """Raised when a configured provider request fails safely."""


class ModelGatewayRefusalError(ModelGatewayError):
    """Raised when a provider returns a structured-output refusal."""


class ModelGatewayExhaustedError(ModelGatewayError):
    """Raised when a deterministic fake has no response left."""
