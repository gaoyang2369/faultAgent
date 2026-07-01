"""Contracts for Phase 4.3 planner-gated execution preview."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PLANNER_GATE_SCHEMA_VERSION = "planner_gate.v1"

PlannerGateMode = Literal["disabled", "dry_run", "active"]
PlannerGateExecutionSource = Literal["legacy_policy", "planner_gated"]


class PlannerGateDecision(BaseModel):
    schema_version: str = PLANNER_GATE_SCHEMA_VERSION
    mode: PlannerGateMode = "disabled"
    eligible: bool = False
    dry_run_eligible: bool = False
    selected_execution_source: PlannerGateExecutionSource = "legacy_policy"
    allowed_task_family: bool = False
    task_family: str = ""
    primary_task_type: str = ""
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    required_diff_status: list[str] = Field(default_factory=list)
    observed_diff_status: str = ""
    observed_diff_severity: str = ""
    allowed_runtime_tools: list[str] = Field(default_factory=list)
    planner_runtime_tools: list[str] = Field(default_factory=list)
    final_runtime_tools: list[str] = Field(default_factory=list)
    final_enabled_nodes: list[str] = Field(default_factory=list)
    active_scope: list[str] = Field(default_factory=list)
    fallback_to_legacy: bool = True
    safety_summary: dict[str, Any] = Field(default_factory=dict)
