"""Contracts for Phase 4.1 shadow planning."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PLANNING_INPUT_SCHEMA_VERSION = "planning_input.v1"
PLANNING_DECISION_SCHEMA_VERSION = "planning_decision.v1"

NodeState = Literal["enabled", "skipped", "blocked", "shadow_only"]
ExpectedOutput = Literal["answer", "report", "workorder_decision", "clarification"]


class PlanningInput(BaseModel):
    """Inputs consumed by the deterministic shadow planner."""

    schema_version: str = PLANNING_INPUT_SCHEMA_VERSION
    message: str = ""
    request_payload_summary: dict[str, Any] = Field(default_factory=dict)
    auth_summary: dict[str, Any] = Field(default_factory=dict)
    resolved_context: dict[str, Any] = Field(default_factory=dict)
    goal_set: dict[str, Any] = Field(default_factory=dict)
    primary_task_type: str = ""
    intent_stack: list[str] = Field(default_factory=list)
    task_family: str = ""
    referenced_artifact_id: str | None = None
    referenced_report_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class NodePlan(BaseModel):
    """Shadow recommendation for one workflow node."""

    node: str
    desired_state: NodeState = "skipped"
    reason: str = ""
    source_goals: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)


class EvidencePlan(BaseModel):
    """Shadow evidence requirements and reuse/refresh guidance."""

    required_evidence: list[str] = Field(default_factory=list)
    reusable_evidence: list[str] = Field(default_factory=list)
    stale_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    refresh_required: bool = False
    disclosure_required: list[str] = Field(default_factory=list)


class ToolPlan(BaseModel):
    """Shadow tool candidates and authorized runtime-tool summary."""

    candidate_tools: list[str] = Field(default_factory=list)
    authorized_runtime_tools: list[str] = Field(default_factory=list)
    denied_tools: list[dict[str, Any]] = Field(default_factory=list)
    whitelist_source: str = "legacy_runtime_tools"
    permission_summary: dict[str, Any] = Field(default_factory=dict)


class OutputPlan(BaseModel):
    """Shadow output shape and answer boundaries."""

    expected_output: ExpectedOutput = "answer"
    required_disclosures: list[str] = Field(default_factory=list)
    report_boundary: str | None = None
    workorder_boundary: str | None = None
    final_answer_guardrails: list[str] = Field(default_factory=list)


class PlanningDecision(BaseModel):
    """Phase 4.1 shadow planner output that must not affect execution."""

    schema_version: str = PLANNING_DECISION_SCHEMA_VERSION
    planner_mode: Literal["shadow"] = "shadow"
    nodes: list[NodePlan] = Field(default_factory=list)
    evidence_plan: EvidencePlan = Field(default_factory=EvidencePlan)
    tool_plan: ToolPlan = Field(default_factory=ToolPlan)
    output_plan: OutputPlan = Field(default_factory=OutputPlan)
    legacy_projection: dict[str, Any] = Field(default_factory=dict)
    planner_warnings: list[str] = Field(default_factory=list)
    planner_summary: str = ""
