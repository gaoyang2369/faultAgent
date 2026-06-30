"""Contracts for Phase 4.2 policy diff evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PLANNING_DIFF_SCHEMA_VERSION = "planning_diff.v1"

DiffMode = Literal["shadow_vs_legacy"]
DiffSeverity = Literal["none", "info", "warning", "error", "critical"]
OverallStatus = Literal["aligned", "acceptable_diff", "needs_review", "unsafe_mismatch"]
NodeState = Literal["enabled", "skipped", "blocked", "shadow_only", "unknown"]


class NodeDiff(BaseModel):
    node: str
    legacy_state: NodeState = "unknown"
    shadow_state: NodeState = "unknown"
    diff_type: str = "exact_match"
    severity: DiffSeverity = "none"
    reason: str = ""
    safety_related: bool = False


class ToolDiff(BaseModel):
    tool: str
    in_legacy_runtime_tools: bool = False
    in_shadow_candidate_tools: bool = False
    in_shadow_authorized_tools: bool = False
    diff_type: str = "exact_match"
    severity: DiffSeverity = "none"
    reason: str = ""
    safety_related: bool = False


class EvidenceDiff(BaseModel):
    evidence_key: str
    legacy_requirement: str | None = None
    shadow_requirement: str | None = None
    diff_type: str = "exact_match"
    severity: DiffSeverity = "none"
    reason: str = ""
    safety_related: bool = False


class OutputDiff(BaseModel):
    output_key: str
    legacy_boundary: str | None = None
    shadow_boundary: str | None = None
    diff_type: str = "exact_match"
    severity: DiffSeverity = "none"
    reason: str = ""
    safety_related: bool = False


class SafetyDiff(BaseModel):
    safety_key: str
    legacy_value: Any = None
    shadow_value: Any = None
    diff_type: str = "exact_match"
    severity: DiffSeverity = "none"
    reason: str = ""
    safety_related: bool = True


class PlanningDiff(BaseModel):
    schema_version: str = PLANNING_DIFF_SCHEMA_VERSION
    diff_mode: DiffMode = "shadow_vs_legacy"
    overall_status: OverallStatus = "aligned"
    severity: DiffSeverity = "none"
    node_diffs: list[NodeDiff] = Field(default_factory=list)
    tool_diffs: list[ToolDiff] = Field(default_factory=list)
    evidence_diffs: list[EvidenceDiff] = Field(default_factory=list)
    output_diffs: list[OutputDiff] = Field(default_factory=list)
    safety_diffs: list[SafetyDiff] = Field(default_factory=list)
    summary: str = ""
    counters: dict[str, int] = Field(default_factory=dict)
    migration_readiness: dict[str, Any] = Field(default_factory=dict)
