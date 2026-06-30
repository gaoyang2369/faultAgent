"""Shadow planning contracts and deterministic helpers."""

from .contracts import (
    EvidencePlan,
    NodePlan,
    OutputPlan,
    PlanningDecision,
    PlanningInput,
    ToolPlan,
)
from .diff_contracts import EvidenceDiff, NodeDiff, OutputDiff, PlanningDiff, SafetyDiff, ToolDiff
from .diff_evaluator import build_planning_diff
from .diff_summaries import summarize_planning_diff
from .gate import apply_planner_gate_to_decision, build_planner_gate, summarize_planner_gate
from .gate_contracts import PlannerGateDecision
from .shadow_planner import attach_shadow_plan_summary, build_planning_input, build_shadow_plan, build_shadow_plan_for_decision
from .summaries import summarize_shadow_plan

__all__ = [
    "EvidenceDiff",
    "EvidencePlan",
    "NodeDiff",
    "NodePlan",
    "OutputDiff",
    "OutputPlan",
    "PlanningDiff",
    "PlanningDecision",
    "PlanningInput",
    "PlannerGateDecision",
    "SafetyDiff",
    "ToolDiff",
    "ToolPlan",
    "build_planning_input",
    "build_shadow_plan",
    "build_shadow_plan_for_decision",
    "build_planning_diff",
    "build_planner_gate",
    "apply_planner_gate_to_decision",
    "attach_shadow_plan_summary",
    "summarize_shadow_plan",
    "summarize_planning_diff",
    "summarize_planner_gate",
]
