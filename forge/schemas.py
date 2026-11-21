"""Output contracts for every agent.

Unknown fields are rejected and file paths must stay inside the repo.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _rel_path(value: str) -> str:
    """Repository-relative POSIX path. Rejects absolute paths and traversal."""
    v = value.strip().replace("\\", "/")
    while v.startswith("./"):
        v = v[2:]
    if not v or v.startswith("/") or ".." in v.split("/") or ":" in v.split("/")[0]:
        raise ValueError(f"path must be repository-relative without '..': {value!r}")
    return v


RelPath = Annotated[str, AfterValidator(_rel_path)]


class AcceptanceCriterion(Contract):
    id: str = Field(pattern=r"^AC-\d+$")
    given: str
    when: str
    then: str


class PlanTask(Contract):
    id: str = Field(pattern=r"^T-\d+$")
    title: str
    description: str
    criteria: list[str] = Field(default_factory=list, description="AC ids this task satisfies")


class AnalysisOutput(Contract):
    summary: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _tasks_reference_known_criteria(self) -> AnalysisOutput:
        known = {c.id for c in self.acceptance_criteria}
        for task in self.tasks:
            unknown = set(task.criteria) - known
            if unknown:
                raise ValueError(f"task {task.id} references unknown criteria {sorted(unknown)}")
        return self


class ChangeType(str, Enum):
    modify = "modify"
    create = "create"


class ImpactedComponent(Contract):
    path: RelPath
    symbols: list[str] = Field(default_factory=list)
    change_type: ChangeType
    reason: str


class OutOfScope(Contract):
    path: str
    reason: str


class DesignOutput(Contract):
    summary: str
    approach: str
    impacted_components: list[ImpactedComponent] = Field(min_length=1)
    out_of_scope: list[OutOfScope] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @property
    def change_scope(self) -> list[str]:
        """The file allowlist the developer is bound to."""
        return sorted({c.path for c in self.impacted_components})


class ScopeRuling(Contract):
    path: str
    decision: Literal["approve", "reject"]
    reason: str


class ScopeArbitrationOutput(Contract):
    rulings: list[ScopeRuling] = Field(min_length=1)


class Edit(Contract):
    """Search/replace edit. An empty `search` creates a new file with `replace` as content."""

    path: RelPath
    search: str
    replace: str


class ScopeExpansionRequest(Contract):
    path: RelPath
    justification: str


class DeveloperOutput(Contract):
    rationale: str
    edits: list[Edit] = Field(default_factory=list)
    scope_requests: list[ScopeExpansionRequest] = Field(default_factory=list)
    no_change_reason: str = Field(
        default="", description="Set only when the reported problem cannot be fixed by changing code"
    )

    @model_validator(mode="after")
    def _non_empty(self) -> DeveloperOutput:
        if not self.edits and not self.scope_requests and not self.no_change_reason.strip():
            raise ValueError("developer must return edits, scope_requests, or a no_change_reason")
        return self


class GeneratedTest(Contract):
    path: RelPath
    content: str
    criteria: list[str] = Field(min_length=1, description="AC ids covered by this file")


class QAOutput(Contract):
    tests: list[GeneratedTest] = Field(min_length=1)
    notes: str = ""


class QARevisionOutput(Contract):
    """QA's ruling when the developer disputes a failing QA-written test."""

    verdict: Literal["test_wrong", "code_wrong"]
    explanation: str
    tests: list[GeneratedTest] = Field(
        default_factory=list, description="Full corrected content of QA test files; required when verdict is test_wrong"
    )

    @model_validator(mode="after")
    def _tests_when_wrong(self) -> QARevisionOutput:
        if self.verdict == "test_wrong" and not self.tests:
            raise ValueError("verdict test_wrong requires the corrected test files")
        return self


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


SEVERITY_RANK = {Severity.low: 1, Severity.medium: 2, Severity.high: 3, Severity.critical: 4}


class SecurityFinding(Contract):
    id: str
    severity: Severity
    title: str
    path: str
    line: int | None = None
    cwe: str | None = None
    source: Literal["bandit", "secrets", "policy", "llm"]
    pre_existing: bool = False
    recommendation: str = ""


class SecurityReviewOutput(Contract):
    """LLM security review. Supplements scanners; never suppresses their findings."""

    findings: list[SecurityFinding] = Field(default_factory=list)
    summary: str


class RubricScore(Contract):
    criterion: Literal[
        "requirements_coverage", "correctness", "test_quality", "security", "scope_discipline", "code_quality"
    ]
    score: int = Field(ge=1, le=5)
    justification: str


class CriterionCoverage(Contract):
    criterion: str
    status: Literal["covered", "partial", "missing"]
    evidence: str


class ReviewOutput(Contract):
    summary: str
    rubric: list[RubricScore] = Field(min_length=1)
    coverage: list[CriterionCoverage] = Field(default_factory=list)
    known_risks: list[str] = Field(default_factory=list)
    recommendation: Literal["approve", "request_changes"]


class ReleaseOutput(Contract):
    title: str
    release_notes: str
    rollback_plan: str
    risk_level: Literal["low", "medium", "high"]
    deployment_checks: list[str] = Field(default_factory=list)


AGENT_CONTRACTS: dict[str, type[Contract]] = {
    "analyst": AnalysisOutput,
    "architect": DesignOutput,
    "arbiter": ScopeArbitrationOutput,
    "developer": DeveloperOutput,
    "qa": QAOutput,
    "security": SecurityReviewOutput,
    "reviewer": ReviewOutput,
    "release": ReleaseOutput,
}
