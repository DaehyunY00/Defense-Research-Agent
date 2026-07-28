"""Claude structured-output gateway and key-only runtime configuration."""

import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Protocol, cast

from anthropic import Anthropic
from pydantic import Field, SecretStr, ValidationError, field_validator

from defense_research_agent.agents.model_gateway import (
    ModelGateway,
    ModelGatewayError,
    ModelGatewayOutputError,
    ModelGatewayProviderError,
    ModelGatewayRefusalError,
    ModelMessage,
    ModelMessageRole,
    StructuredOutputT,
)
from defense_research_agent.domain import (
    DomainModel,
    JsonObject,
    ModelProvider,
    ModelRoute,
    ResearchRole,
)

_AUDIT_METADATA_KEYS = frozenset(
    {
        "candidate_id",
        "evaluator",
        "phase",
        "project_id",
        "prompt_version",
        "role",
        "task_id",
    }
)
_ROLE_MODEL_ENVIRONMENT_KEYS: dict[ResearchRole, str] = {
    role: f"DEFENSE_RESEARCH_CLAUDE_MODEL_{role.value.upper()}" for role in ResearchRole
}
_DEFAULT_ROLE_MODEL_IDS: dict[ResearchRole, str] = {
    ResearchRole.MAIN_RESEARCHER: "claude-opus-5",
    ResearchRole.LITERATURE_RESEARCHER: "claude-haiku-4-5",
    ResearchRole.CURRENT_ISSUE_RESEARCHER: "claude-haiku-4-5",
    ResearchRole.METHODOLOGY_RESEARCHER: "claude-sonnet-5",
    ResearchRole.DEVELOPER_RESEARCHER: "claude-sonnet-5",
    ResearchRole.EVIDENCE_AUDITOR: "claude-opus-5",
    ResearchRole.CRITICAL_REVIEWER: "claude-opus-5",
}
_DEFAULT_ROLE_MAX_OUTPUT_TOKENS: dict[ResearchRole, int] = {
    ResearchRole.MAIN_RESEARCHER: 16_384,
    ResearchRole.LITERATURE_RESEARCHER: 8_192,
    ResearchRole.CURRENT_ISSUE_RESEARCHER: 8_192,
    ResearchRole.METHODOLOGY_RESEARCHER: 12_288,
    ResearchRole.DEVELOPER_RESEARCHER: 12_288,
    ResearchRole.EVIDENCE_AUDITOR: 12_288,
    ResearchRole.CRITICAL_REVIEWER: 12_288,
}


class AnthropicMessagesClient(Protocol):
    """Small SDK surface injected into tests without network access."""

    def parse(self, **kwargs: object) -> object:
        """Create one structured message response."""


class AnthropicClient(Protocol):
    """Structural subset of the official Anthropic client."""

    @property
    def messages(self) -> AnthropicMessagesClient:
        """Return the structured Messages API surface."""
        ...


class AnthropicRuntimeSettings(DomainModel):
    """Deployment settings with safe defaults so only the API key is required."""

    api_key: SecretStr
    timeout_seconds: float = Field(default=90.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    role_model_ids: dict[ResearchRole, str] = Field(
        default_factory=lambda: dict(_DEFAULT_ROLE_MODEL_IDS)
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("ANTHROPIC_API_KEY must not be blank")
        return value

    @field_validator("role_model_ids")
    @classmethod
    def validate_role_models(
        cls,
        value: dict[ResearchRole, str],
    ) -> dict[ResearchRole, str]:
        if set(value) != set(ResearchRole):
            raise ValueError("Claude model routes must cover exactly the seven research roles")
        normalized = {role: model_id.strip() for role, model_id in value.items()}
        if any(not model_id for model_id in normalized.values()):
            raise ValueError("Claude model IDs must not be blank")
        return normalized

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "AnthropicRuntimeSettings":
        """Load one required secret and optional operational overrides."""
        source = os.environ if environment is None else environment
        api_key = source.get("ANTHROPIC_API_KEY", "")
        timeout_seconds = _parse_float_environment(
            source,
            "DEFENSE_RESEARCH_CLAUDE_TIMEOUT_SECONDS",
            90.0,
        )
        max_retries = _parse_int_environment(
            source,
            "DEFENSE_RESEARCH_CLAUDE_MAX_RETRIES",
            2,
        )
        role_model_ids = {
            role: source.get(environment_key, default_model)
            for role, default_model in _DEFAULT_ROLE_MODEL_IDS.items()
            for environment_key in (_ROLE_MODEL_ENVIRONMENT_KEYS[role],)
        }
        return cls(
            api_key=SecretStr(api_key),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            role_model_ids=role_model_ids,
        )

    def model_routes(self) -> dict[ResearchRole, ModelRoute]:
        """Build provider-neutral role routes used by the research agents."""
        return {
            role: ModelRoute(
                provider=ModelProvider.ANTHROPIC,
                model_id=self.role_model_ids[role],
                max_output_tokens=_DEFAULT_ROLE_MAX_OUTPUT_TOKENS[role],
            )
            for role in ResearchRole
        }


@dataclass(frozen=True, slots=True)
class AnthropicModelCallAudit:
    """Secret-free provider call summary suitable for a later audit repository."""

    task_type: str
    model_id: str
    output_schema_name: str
    status: str
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    request_id: str | None
    metadata: JsonObject


class AnthropicModelGateway(ModelGateway):
    """Use the official Claude SDK Pydantic helper and revalidate every result."""

    def __init__(
        self,
        route: ModelRoute,
        client: AnthropicClient,
    ) -> None:
        if route.provider is not ModelProvider.ANTHROPIC:
            raise ValueError("AnthropicModelGateway requires an anthropic ModelRoute")
        self._route = route
        self._client = client
        self._audit_records: list[AnthropicModelCallAudit] = []
        self._audit_lock = Lock()

    @property
    def route(self) -> ModelRoute:
        """Return the immutable logical route used by this gateway."""
        return self._route

    @property
    def audit_records(self) -> tuple[AnthropicModelCallAudit, ...]:
        """Return a thread-safe snapshot without prompts or secrets."""
        with self._audit_lock:
            return tuple(self._audit_records)

    def generate_structured(
        self,
        task_type: str,
        messages: Sequence[ModelMessage],
        output_schema: type[StructuredOutputT],
        metadata: JsonObject,
    ) -> StructuredOutputT:
        """Call Claude once through structured outputs and validate again locally."""
        system, provider_messages = _convert_messages(messages)
        request: dict[str, object] = {
            "model": self._route.model_id,
            "max_tokens": self._route.max_output_tokens,
            "messages": provider_messages,
            "output_format": output_schema,
        }
        if system:
            request["system"] = system
        if self._route.temperature is not None:
            request["temperature"] = self._route.temperature

        started_at = time.perf_counter()
        response: object | None = None
        status = "provider_error"
        try:
            response = self._client.messages.parse(**request)
            stop_reason = _optional_string_attribute(response, "stop_reason")
            if stop_reason == "refusal":
                status = "refusal"
                raise ModelGatewayRefusalError("Claude refused the structured request")
            if stop_reason == "max_tokens":
                status = "output_error"
                raise ModelGatewayOutputError(
                    "Claude reached max_tokens before completing structured output"
                )
            parsed_output = getattr(response, "parsed_output", None)
            if parsed_output is None:
                status = "output_error"
                raise ModelGatewayOutputError("Claude returned no parsed structured output")
            validated = output_schema.model_validate(parsed_output)
            status = "success"
            return validated
        except ModelGatewayError:
            raise
        except (ValidationError, ValueError) as error:
            status = "output_error"
            raise ModelGatewayOutputError(
                f"Claude output failed {output_schema.__name__} validation"
            ) from error
        except Exception as error:
            status = "provider_error"
            raise ModelGatewayProviderError(
                f"Claude provider request failed ({type(error).__name__})"
            ) from error
        finally:
            self._record_audit(
                task_type=task_type,
                output_schema_name=output_schema.__name__,
                status=status,
                duration_ms=max(0, round((time.perf_counter() - started_at) * 1_000)),
                response=response,
                metadata=metadata,
            )

    def _record_audit(
        self,
        *,
        task_type: str,
        output_schema_name: str,
        status: str,
        duration_ms: int,
        response: object | None,
        metadata: JsonObject,
    ) -> None:
        usage = getattr(response, "usage", None)
        record = AnthropicModelCallAudit(
            task_type=task_type,
            model_id=self._route.model_id,
            output_schema_name=output_schema_name,
            status=status,
            duration_ms=duration_ms,
            input_tokens=_optional_int_attribute(usage, "input_tokens"),
            output_tokens=_optional_int_attribute(usage, "output_tokens"),
            request_id=_optional_string_attribute(response, "_request_id"),
            metadata={key: value for key, value in metadata.items() if key in _AUDIT_METADATA_KEYS},
        )
        with self._audit_lock:
            self._audit_records.append(record)


def build_anthropic_role_gateways(
    settings: AnthropicRuntimeSettings,
    *,
    client: AnthropicClient | None = None,
) -> dict[ResearchRole, AnthropicModelGateway]:
    """Create one role-bound gateway while sharing the SDK connection pool."""
    shared_client = (
        client
        if client is not None
        else cast(
            AnthropicClient,
            Anthropic(
                api_key=settings.api_key.get_secret_value(),
                timeout=settings.timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )
    )
    return {
        role: AnthropicModelGateway(route, shared_client)
        for role, route in settings.model_routes().items()
    }


def default_anthropic_role_model_ids() -> dict[ResearchRole, str]:
    """Return a copy of the deployable default mapping."""
    return dict(_DEFAULT_ROLE_MODEL_IDS)


def anthropic_role_model_environment_keys() -> dict[ResearchRole, str]:
    """Return documented environment override names for every role."""
    return dict(_ROLE_MODEL_ENVIRONMENT_KEYS)


def _convert_messages(
    messages: Sequence[ModelMessage],
) -> tuple[str, list[dict[str, str]]]:
    system_parts = [
        message.content for message in messages if message.role is ModelMessageRole.SYSTEM
    ]
    provider_messages = [
        {"role": "user", "content": message.content}
        for message in messages
        if message.role is ModelMessageRole.USER
    ]
    if not provider_messages:
        raise ModelGatewayProviderError("Claude requests require at least one user message")
    return "\n\n".join(system_parts), provider_messages


def _optional_string_attribute(value: object | None, name: str) -> str | None:
    attribute = getattr(value, name, None)
    return attribute if isinstance(attribute, str) else None


def _optional_int_attribute(value: object | None, name: str) -> int | None:
    attribute = getattr(value, name, None)
    return attribute if isinstance(attribute, int) and not isinstance(attribute, bool) else None


def _parse_float_environment(
    environment: Mapping[str, str],
    key: str,
    default: float,
) -> float:
    raw_value = environment.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return float(raw_value)
    except ValueError as error:
        raise ValueError(f"{key} must be a number") from error


def _parse_int_environment(
    environment: Mapping[str, str],
    key: str,
    default: int,
) -> int:
    raw_value = environment.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return int(raw_value)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer") from error
