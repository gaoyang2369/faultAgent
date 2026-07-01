"""Shadow planning contracts and deterministic helpers."""

from .contracts import (
    EvidencePlan,
    NodePlan,
    OutputPlan,
    PlanningDecision,
    PlanningInput,
    ToolPlan,
)
from .action_readiness import (
    WorkorderActionReadiness,
    build_workorder_action_readiness,
    summarize_workorder_action_readiness,
)
from .diff_contracts import EvidenceDiff, NodeDiff, OutputDiff, PlanningDiff, SafetyDiff, ToolDiff
from .diff_evaluator import build_planning_diff
from .diff_summaries import summarize_planning_diff
from .diagnosis_readiness import DiagnosisReadiness, build_diagnosis_readiness, summarize_diagnosis_readiness
from .gate import apply_planner_gate_to_decision, build_planner_gate, summarize_planner_gate
from .gate_contracts import PlannerGateDecision
from .manual_confirmation import (
    ManualConfirmationRequirement,
    build_manual_confirmation_requirement,
    contains_forbidden_execution_phrase,
    summarize_manual_confirmation_requirement,
)
from .shadow_planner import attach_shadow_plan_summary, build_planning_input, build_shadow_plan, build_shadow_plan_for_decision
from .summaries import summarize_shadow_plan

__all__ = [
    "WorkorderActionReadiness",
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
    "ManualConfirmationRequirement",
    "DiagnosisReadiness",
    "SafetyDiff",
    "ToolDiff",
    "ToolPlan",
    "build_workorder_action_readiness",
    "build_planning_input",
    "build_shadow_plan",
    "build_shadow_plan_for_decision",
    "build_planning_diff",
    "build_diagnosis_readiness",
    "build_manual_confirmation_requirement",
    "build_planner_gate",
    "apply_planner_gate_to_decision",
    "attach_shadow_plan_summary",
    "contains_forbidden_execution_phrase",
    "summarize_workorder_action_readiness",
    "summarize_shadow_plan",
    "summarize_planning_diff",
    "summarize_diagnosis_readiness",
    "summarize_manual_confirmation_requirement",
    "summarize_planner_gate",
]
