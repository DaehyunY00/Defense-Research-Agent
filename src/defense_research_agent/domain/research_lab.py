"""Domain contracts for the seven-role research-lab workflow."""

from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from defense_research_agent.domain.common import (
    Checksum,
    Confidence,
    DomainModel,
    EntityId,
    JsonObject,
    Label,
)
from defense_research_agent.domain.data_analysis import DataAnalysisRequest

type RequiredText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
]


class ResearchRole(StrEnum):
    """Stable role identifiers used for routing, policy, and audit records."""

    MAIN_RESEARCHER = "main_researcher"
    LITERATURE_RESEARCHER = "literature_researcher"
    CURRENT_ISSUE_RESEARCHER = "current_issue_researcher"
    METHODOLOGY_RESEARCHER = "methodology_researcher"
    DEVELOPER_RESEARCHER = "developer_researcher"
    EVIDENCE_AUDITOR = "evidence_auditor"
    CRITICAL_REVIEWER = "critical_reviewer"


SPECIALIST_RESEARCH_ROLES: tuple[ResearchRole, ...] = (
    ResearchRole.LITERATURE_RESEARCHER,
    ResearchRole.CURRENT_ISSUE_RESEARCHER,
    ResearchRole.METHODOLOGY_RESEARCHER,
    ResearchRole.DEVELOPER_RESEARCHER,
)

REVIEW_ROLES: tuple[ResearchRole, ...] = (
    ResearchRole.EVIDENCE_AUDITOR,
    ResearchRole.CRITICAL_REVIEWER,
)


class ModelProvider(StrEnum):
    """Provider names understood by deployment-time gateway factories."""

    FAKE = "fake"
    ANTHROPIC = "anthropic"


class ToolCapability(StrEnum):
    """Allow-listed capabilities; these values are policy, not executable commands."""

    ARTIFACT_READ = "artifact_read"
    INTERNAL_CORPUS_SEARCH = "internal_corpus_search"
    EXTERNAL_SOURCE_SEARCH = "external_source_search"
    DATA_ANALYSIS_SANDBOX = "data_analysis_sandbox"
    CODE_SANDBOX = "code_sandbox"


class ModelRoute(DomainModel):
    """Logical model binding kept independent from a provider SDK."""

    provider: ModelProvider
    model_id: Label
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    max_output_tokens: PositiveInt = Field(default=4_096, le=64_000)


class ResearchRoleSpec(DomainModel):
    """One agent's stable responsibility, model route, and tool allow-list."""

    role: ResearchRole
    display_name: Label
    mission: RequiredText
    model_route: ModelRoute
    allowed_tools: list[ToolCapability] = Field(default_factory=list)


class DataSensitivity(StrEnum):
    """PoC data boundary; non-public material is intentionally unsupported."""

    PUBLIC_ONLY = "public_only"


class ResearchBrief(DomainModel):
    """Human-authored request accepted by the main researcher."""

    project_id: EntityId
    question: RequiredText
    objective: RequiredText
    scope: list[Label] = Field(default_factory=list, max_length=20)
    constraints: list[Label] = Field(default_factory=list, max_length=20)
    deliverables: list[Label] = Field(default_factory=list, min_length=1, max_length=20)
    evidence_start_date: date | None = None
    evidence_end_date: date | None = None
    data_sensitivity: DataSensitivity = DataSensitivity.PUBLIC_ONLY
    human_approval_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_evidence_date_range(self) -> "ResearchBrief":
        if (
            self.evidence_start_date is not None
            and self.evidence_end_date is not None
            and self.evidence_start_date > self.evidence_end_date
        ):
            raise ValueError("evidence_start_date must be on or before evidence_end_date")
        return self


class ResearchTask(DomainModel):
    """A bounded assignment produced by the main researcher."""

    task_id: EntityId
    role: ResearchRole
    title: Label
    instructions: RequiredText
    expected_output: RequiredText
    requested_tools: list[ToolCapability] = Field(default_factory=list, max_length=10)
    search_queries: list[Label] = Field(default_factory=list, max_length=5)
    policy_domains: list[Label] = Field(default_factory=list, max_length=10)
    data_analysis_requests: list[DataAnalysisRequest] = Field(
        default_factory=list,
        max_length=10,
    )

    @model_validator(mode="after")
    def validate_data_analysis_requests(self) -> "ResearchTask":
        request_ids = [request.request_id for request in self.data_analysis_requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("data analysis request_id values must be unique within a task")
        if self.data_analysis_requests and (
            self.role is not ResearchRole.METHODOLOGY_RESEARCHER
            or ToolCapability.DATA_ANALYSIS_SANDBOX not in self.requested_tools
        ):
            raise ValueError(
                "data analysis requests require the methodology role and data_analysis_sandbox tool"
            )
        if (
            ToolCapability.DATA_ANALYSIS_SANDBOX in self.requested_tools
            and not self.data_analysis_requests
        ):
            raise ValueError("data_analysis_sandbox requires at least one analysis request")
        return self


class ResearchPlan(DomainModel):
    """Validated fan-out plan; reviewer tasks are added deterministically later."""

    project_id: EntityId
    rationale: RequiredText
    tasks: list[ResearchTask] = Field(min_length=1, max_length=16)
    success_criteria: list[Label] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_specialist_assignments(self) -> "ResearchPlan":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("research plan task_id values must be unique")
        roles = [task.role for task in self.tasks]
        if len(roles) != len(set(roles)):
            raise ValueError("research plan can assign at most one task to each specialist role")
        unsupported = set(roles) - set(SPECIALIST_RESEARCH_ROLES)
        if unsupported:
            raise ValueError(
                "research plan tasks must target specialist roles only: "
                f"{sorted(role.value for role in unsupported)}"
            )
        return self


class EvidenceCitation(DomainModel):
    """Traceable evidence reference supplied by a worker agent."""

    evidence_id: EntityId
    title: Label
    source_type: Label
    locator: Label | None = None
    source_url: str | None = None
    untrusted_external_content: bool = False


class ResearchToolEvidence(DomainModel):
    """Evidence returned by an allow-listed deterministic tool adapter."""

    evidence_id: EntityId
    capability: ToolCapability
    title: Label
    excerpt: str | None = Field(default=None, max_length=4_000)
    source_type: Label
    locator: Label | None = None
    source_url: str | None = None
    untrusted_external_content: bool = False
    metadata: JsonObject = Field(default_factory=dict)


class ResearchToolFailure(DomainModel):
    """Sanitized adapter or provider failure attached to one task."""

    capability: ToolCapability
    code: Label
    message: str = Field(max_length=500)
    retryable: bool = False


class ResearchToolOutput(DomainModel):
    """One adapter execution before results are merged for an agent."""

    capability: ToolCapability
    evidence: list[ResearchToolEvidence] = Field(default_factory=list, max_length=100)
    failures: list[ResearchToolFailure] = Field(default_factory=list, max_length=30)


class ResearchToolContext(DomainModel):
    """Merged, auditable tool data supplied to exactly one agent task."""

    project_id: EntityId
    task_id: EntityId
    role: ResearchRole
    requested_tools: list[ToolCapability] = Field(default_factory=list)
    evidence: list[ResearchToolEvidence] = Field(default_factory=list, max_length=200)
    failures: list[ResearchToolFailure] = Field(default_factory=list, max_length=50)


class ResearchFinding(DomainModel):
    """One claim with explicit confidence, evidence, and caveats."""

    finding_id: EntityId
    statement: RequiredText
    evidence: list[EvidenceCitation] = Field(default_factory=list, max_length=50)
    confidence: Confidence
    caveats: list[Label] = Field(default_factory=list, max_length=20)


class ArtifactKind(StrEnum):
    """Developer and analyst artifacts that can be proposed by a worker."""

    DESIGN = "design"
    CODE_PATCH = "code_patch"
    TEST_PLAN = "test_plan"
    NOTEBOOK = "notebook"
    DATASET = "dataset"


class CodeFileOperation(StrEnum):
    """Safe file mutations supported inside an ephemeral sandbox."""

    CREATE = "create"
    REPLACE = "replace"


class CodeFileChange(DomainModel):
    """One bounded text-file change proposed by the developer agent."""

    relative_path: str = Field(min_length=1, max_length=500)
    operation: CodeFileOperation
    content: str = Field(max_length=200_000)
    expected_sha256: Checksum | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.strip()
        path = PurePosixPath(normalized)
        if (
            not normalized
            or "\\" in normalized
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("code change path must be a safe POSIX relative path")
        if path.parts[0] in {"data", "artifacts", ".git", ".venv"}:
            raise ValueError("code change path targets a protected project area")
        return normalized

    @model_validator(mode="after")
    def validate_expected_checksum(self) -> "CodeFileChange":
        if self.operation is CodeFileOperation.REPLACE and self.expected_sha256 is None:
            raise ValueError("replace operation requires expected_sha256")
        if self.operation is CodeFileOperation.CREATE and self.expected_sha256 is not None:
            raise ValueError("create operation cannot include expected_sha256")
        return self


class CodeSandboxCheck(StrEnum):
    """Fixed validation programs; arbitrary shell commands are unsupported."""

    PYTHON_COMPILE = "python_compile"
    PYTEST = "pytest"


class CodeSandboxValidation(DomainModel):
    """One allow-listed subprocess request with path-only arguments."""

    check: CodeSandboxCheck
    targets: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    timeout_seconds: PositiveInt = Field(default=30, le=120)

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, values: list[str]) -> list[str]:
        normalized_targets: list[str] = []
        for value in values:
            normalized = value.strip()
            path = PurePosixPath(normalized)
            if (
                not normalized
                or "\\" in normalized
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise ValueError("sandbox validation targets must be safe relative paths")
            normalized_targets.append(normalized)
        return list(dict.fromkeys(normalized_targets))


class CodeSandboxStatus(StrEnum):
    """Outcome of validating one proposed code artifact."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class CodeSandboxCheckResult(DomainModel):
    """Sanitized result of one fixed validation process."""

    check: CodeSandboxCheck
    targets: list[str] = Field(default_factory=list)
    passed: bool
    exit_code: int | None = None
    elapsed_ms: NonNegativeInt
    output: str = Field(default="", max_length=8_000)


class CodeSandboxResult(DomainModel):
    """Ephemeral execution record; no change is applied to the source project."""

    project_id: EntityId
    task_id: EntityId
    artifact_id: EntityId
    status: CodeSandboxStatus
    changed_paths: list[str] = Field(default_factory=list, max_length=20)
    unified_diff: str = Field(default="", max_length=50_000)
    check_results: list[CodeSandboxCheckResult] = Field(default_factory=list, max_length=20)
    failure_code: Label | None = None
    failure_message: str | None = Field(default=None, max_length=500)
    workspace_lifecycle: Literal["ephemeral"] = "ephemeral"
    applied_to_source: Literal[False] = False
    deployed: Literal[False] = False

    @model_validator(mode="after")
    def validate_status_fields(self) -> "CodeSandboxResult":
        if self.status is CodeSandboxStatus.PASSED and (
            self.failure_code is not None or self.failure_message is not None
        ):
            raise ValueError("passed sandbox result cannot include a failure")
        if self.status is not CodeSandboxStatus.PASSED and self.failure_code is None:
            raise ValueError("failed or blocked sandbox result requires failure_code")
        return self


class ProposedArtifact(DomainModel):
    """A reviewable artifact proposal; it never implies deployment or approval."""

    artifact_id: EntityId
    kind: ArtifactKind
    title: Label
    summary: RequiredText
    repository_path: Label | None = None
    validation_commands: list[Label] = Field(default_factory=list, max_length=20)
    code_changes: list[CodeFileChange] = Field(default_factory=list, max_length=10)
    sandbox_validations: list[CodeSandboxValidation] = Field(default_factory=list, max_length=10)
    requires_human_approval: Literal[True] = True

    @model_validator(mode="after")
    def validate_code_artifact_fields(self) -> "ProposedArtifact":
        if (
            self.code_changes or self.sandbox_validations
        ) and self.kind is not ArtifactKind.CODE_PATCH:
            raise ValueError("code changes and sandbox validations require code_patch kind")
        return self


class ResearchAgentResult(DomainModel):
    """Schema-validated output from one specialist or reviewer."""

    project_id: EntityId
    task_id: EntityId
    role: ResearchRole
    summary: RequiredText
    findings: list[ResearchFinding] = Field(min_length=1, max_length=50)
    evidence_gaps: list[Label] = Field(default_factory=list, max_length=30)
    recommendations: list[Label] = Field(default_factory=list, max_length=30)
    proposed_artifacts: list[ProposedArtifact] = Field(default_factory=list, max_length=20)


class ResearchAgentFailure(DomainModel):
    """Sanitized failure preserved without discarding successful peer work."""

    project_id: EntityId
    task_id: EntityId
    role: ResearchRole
    error_type: Label
    message: str = Field(max_length=500)


class ResearchLabReport(DomainModel):
    """Main researcher's final synthesis, still pending explicit human review."""

    project_id: EntityId
    executive_summary: RequiredText
    key_findings: list[ResearchFinding] = Field(default_factory=list, max_length=50)
    disagreements: list[Label] = Field(default_factory=list, max_length=30)
    evidence_gaps: list[Label] = Field(default_factory=list, max_length=30)
    poc_recommendations: list[Label] = Field(default_factory=list, max_length=30)
    next_steps: list[Label] = Field(default_factory=list, max_length=30)
    source_task_ids: list[EntityId] = Field(default_factory=list, max_length=30)
    human_approval_required: Literal[True] = True


class ResearchLabStatus(StrEnum):
    """A lab run cannot become approved through an agent-generated transition."""

    AWAITING_HUMAN_REVIEW = "awaiting_human_review"


class ResearchLabRun(DomainModel):
    """Complete deterministic orchestration envelope returned to a caller."""

    brief: ResearchBrief
    plan: ResearchPlan
    specialist_results: list[ResearchAgentResult] = Field(default_factory=list)
    review_results: list[ResearchAgentResult] = Field(default_factory=list)
    tool_contexts: list[ResearchToolContext] = Field(default_factory=list)
    sandbox_results: list[CodeSandboxResult] = Field(default_factory=list)
    failures: list[ResearchAgentFailure] = Field(default_factory=list)
    final_report: ResearchLabReport
    execution_order: list[ResearchRole] = Field(default_factory=list)
    status: ResearchLabStatus = ResearchLabStatus.AWAITING_HUMAN_REVIEW
