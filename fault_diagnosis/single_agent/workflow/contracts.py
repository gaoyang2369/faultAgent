"""Workflow routing and policy contracts for the single-agent runtime."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """Top-level task types used to select the parent workflow."""

    STATUS_QUERY = "status_query"
    ALARM_TRIAGE = "alarm_triage"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    HEALTH_ASSESSMENT = "health_assessment"
    KNOWLEDGE_QA = "knowledge_qa"
    REPORT_GENERATION = "report_generation"
    ACTION_REQUEST = "action_request"


SubgoalStatus = Literal["ready", "blocked", "skipped"]
RiskLevel = Literal["read_only", "requires_confirmation", "write_action", "high_risk"]
NodeSetting = bool | Literal["conditional"]


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


class TaskRoute(BaseModel):
    """Structured output of the intent router."""

    primary_task_type: TaskType = TaskType.FAULT_DIAGNOSIS
    candidate_task_types: list[TaskType] = Field(default_factory=list)
    intent_stack: list[str] = Field(default_factory=list)
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
