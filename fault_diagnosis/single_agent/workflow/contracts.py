"""Workflow routing and policy contracts for the single-agent runtime."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """Deprecated legacy primary workflow classifier.

    Retained for workflow policy, frontend, eval, artifact, trace and SSE
    compatibility. New internal planning logic should prefer GoalSet,
    TaskFamily, ShadowPlanner, PlanningDiff and PlannerGate projections.
    """

    STATUS_QUERY = "status_query"
    ALARM_TRIAGE = "alarm_triage"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    HEALTH_ASSESSMENT = "health_assessment"
    KNOWLEDGE_QA = "knowledge_qa"
    REPORT_GENERATION = "report_generation"
    ACTION_REQUEST = "action_request"
    PERMISSION_SCOPE_QUERY = "permission_scope_query"


TaskFamily = Literal[
    "knowledge_lookup",
    "runtime_status",
    "diagnosis",
    "reporting",
    "action_or_workorder",
    "meta",
]
TaskFamilySource = Literal[
    "task_type_mapping",
    "goal_hint_fallback",
    "direct_response",
    "unknown_fallback",
]
SubgoalStatus = Literal["ready", "blocked", "skipped"]
RiskLevel = Literal["read_only", "requires_confirmation", "write_action", "high_risk"]
GoalRiskLevel = Literal["read_only", "requires_confirmation", "high_risk"]
GoalExpectedOutput = Literal["answer", "report", "workorder_decision", "clarification"]
GoalSource = Literal["explicit_user_request", "inferred_from_context", "compatibility_projection"]
NodeSetting = bool | Literal["conditional"]
GOAL_SET_SCHEMA_VERSION = "goal_set.v1"


class WorkflowObjects(BaseModel):
    """Structured objects extracted during routing."""

    device_ids: list[str] = Field(default_factory=list)
    alarm_codes: list[str] = Field(default_factory=list)
    system: str | None = None
    location: str | None = None
    metrics: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


class WorkflowTimeWindow(BaseModel):
    """Normalized time-window information for workflow execution."""

    start: str | None = None
    end: str | None = None
    is_inferred: bool = False
    default_strategy: str = "current_status"


class WorkflowSubgoal(BaseModel):
    """One decomposed subgoal under the selected parent workflow."""

    id: str
    type: str
    required: bool = True
    status: SubgoalStatus = "ready"
    missing_slots: list[str] = Field(default_factory=list)


class IntentGoal(BaseModel):
    """One structured user goal independent of workflow tool selection."""

    goal_id: str
    goal_type: str
    description: str
    status: SubgoalStatus = "ready"
    depends_on: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    expected_output: GoalExpectedOutput = "answer"
    risk_level: GoalRiskLevel = "read_only"
    source: GoalSource = "explicit_user_request"
    context_refs: list[str] = Field(default_factory=list)
    reason: str = ""


class GoalSet(BaseModel):
    """Structured goal set projected to the legacy intent stack for compatibility."""

    schema_version: str = GOAL_SET_SCHEMA_VERSION
    primary_goal_id: str | None = None
    goals: list[IntentGoal] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)
    blocked_goals: list[str] = Field(default_factory=list)
    intent_stack_projection: list[str] = Field(default_factory=list)
    goal_summary: str = ""


class TaskFamilyResolution(BaseModel):
    """Coarse task-family mapping used only for observation and migration."""

    task_family: TaskFamily = "diagnosis"
    reason: str = ""
    source: TaskFamilySource = "task_type_mapping"
    warnings: list[str] = Field(default_factory=list)


class TaskRoute(BaseModel):
    """Structured output of the intent router."""

    primary_task_type: TaskType = Field(
        default=TaskType.FAULT_DIAGNOSIS,
        description=(
            "Deprecated compatibility field: legacy primary workflow classifier. "
            "Retained for policy/frontend/eval/artifact compatibility."
        ),
    )
    task_family: TaskFamily = "diagnosis"
    task_family_reason: str = ""
    task_family_source: TaskFamilySource = "task_type_mapping"
    task_family_warnings: list[str] = Field(default_factory=list)
    candidate_task_types: list[TaskType] = Field(
        default_factory=list,
        description="Deprecated compatibility field: legacy alternate task-type projection.",
    )
    intent_stack: list[str] = Field(
        default_factory=list,
        description=(
            "Deprecated compatibility field: legacy policy intent projection built "
            "from GoalSet projection plus legacy candidates."
        ),
    )
    goals: list[IntentGoal] = Field(default_factory=list)
    goal_set: dict[str, Any] = Field(default_factory=dict)
    goal_summary: str = ""
    resolved_context: dict[str, Any] = Field(default_factory=dict)
    context_resolution: dict[str, Any] = Field(default_factory=dict)
    relation_to_previous: str = "new_task"
    plan_mode: str = "normal"
    evidence_mode: str = "collect_new"
    referenced_artifact_id: str | None = None
    referenced_case_id: str | None = None
    required_evidence: list[str] = Field(default_factory=list)
    satisfied_evidence: list[str] = Field(default_factory=list)
    missing_or_stale_evidence: list[str] = Field(default_factory=list)
    should_refresh_runtime_data: bool = False
    action_target: str | None = None
    route_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    user_goal: str = ""
    objects: WorkflowObjects = Field(default_factory=WorkflowObjects)
    time_window: WorkflowTimeWindow = Field(default_factory=WorkflowTimeWindow)
    subgoals: list[WorkflowSubgoal] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "read_only"
    requested_output: str = "answer"
    flags: dict[str, bool] = Field(default_factory=dict)
    action_type: str | None = None

    def has_device_context(self) -> bool:
        """Return whether the route can bind runtime data to a concrete asset."""

        return bool(self.objects.device_ids or self.objects.system or self.objects.location)


class WorkflowPolicy(BaseModel):
    """Policy selected for a parent workflow."""

    policy_id: str
    task_type: TaskType
    workflow_id: str
    required_slots: list[str] = Field(default_factory=list)
    conditional_required_slots: dict[str, list[str]] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    enabled_nodes: dict[str, NodeSetting] = Field(default_factory=dict)
    evidence_requirements: dict[str, bool] = Field(default_factory=dict)
    output_schema: str = ""
    on_missing_evidence: str = "answer_with_uncertainty"
    guardrails: list[str] = Field(default_factory=list)

    def node_setting(self, node_name: str) -> NodeSetting:
        """Return configured node setting, defaulting to disabled."""

        return self.enabled_nodes.get(node_name, False)


class WorkflowPlan(BaseModel):
    """Resolved plan consumed by the orchestration layer."""

    route: TaskRoute
    policy: WorkflowPolicy
    resolved_nodes: dict[str, bool] = Field(default_factory=dict)
    runtime_tools: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
