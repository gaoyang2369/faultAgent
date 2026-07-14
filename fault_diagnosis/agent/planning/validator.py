"""Validation-only guard for canonical execution plans."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from fault_diagnosis.domain.canonical_turn import (
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)

from ..contracts import ExecutionPlan
from .policy_bridge import BLOCKING_FORBIDDEN_TOOLS, GLOBAL_FORBIDDEN_TOOLS


ValidationStatus = Literal["validated", "degraded", "blocked"]


class PlanValidationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    message: str
    node_id: str | None = None
    tool: str | None = None


class PlanValidationResult(BaseModel):
    candidate_plan: ExecutionPlan
    validated_plan: ExecutionPlan
    status: ValidationStatus
    issues: list[PlanValidationIssue] = Field(default_factory=list)
    authorization: dict[str, Any] = Field(default_factory=dict)
    removed_tools: list[str] = Field(default_factory=list)
    approval_requirements: list[dict[str, Any]] = Field(default_factory=list)
    execution_mode: Literal["normal", "draft_only"] = "normal"
    post_execution_confirmation_required: bool = False

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


class PlanValidator:
    """Reject invalid plans; never add, delete, reorder or rewrite Goals/nodes."""

    def validate(
        self,
        *,
        candidate_plan: ExecutionPlan,
        request: CanonicalTurnRequest | None = None,
        authorization: list[GoalAuthorizationDecision] | None = None,
        readiness: list[GoalReadinessDecision] | None = None,
        sources: list[GoalSourceResolution] | None = None,
        require_runtime_inputs: bool = False,
        **legacy: Any,
    ) -> PlanValidationResult:
        if request is None:
            from ..compat.phase1_planning import validate_legacy_candidate

            return validate_legacy_candidate(
                {"candidate_plan": candidate_plan, "require_runtime_inputs": require_runtime_inputs, **legacy},
                PlanValidationResult,
                PlanValidationIssue,
            )
        if authorization is None or readiness is None or sources is None:
            raise TypeError("per-goal decisions are required")
        issues: list[PlanValidationIssue] = []
        canonical_by_id = {goal.goal_id: goal for goal in request.goals}
        plan_by_id = {goal.goal_id: goal for goal in candidate_plan.goals}
        auth = {item.goal_id: item for item in authorization}
        ready = {item.goal_id: item for item in readiness}
        source = {item.goal_id: item for item in sources}

        if list(plan_by_id) != [goal.goal_id for goal in request.goals]:
            issues.append(_error("requested_goals_not_preserved", "Plan must preserve every canonical Goal in order."))
        for goal_id, canonical in canonical_by_id.items():
            planned = plan_by_id.get(goal_id)
            if planned is None:
                continue
            if planned.capability != canonical.capability:
                issues.append(_error("goal_capability_changed", f"Plan changed capability for {goal_id}."))
            if planned.origin != canonical.origin or planned.clause_index != canonical.clause_index:
                issues.append(_error("goal_provenance_changed", f"Plan changed origin or clause index for {goal_id}."))

        for node in candidate_plan.nodes:
            if not node.goal_ids:
                issues.append(_error("node_missing_goal_id", "Every node must trace to at least one Goal.", node_id=node.node_id))
            for goal_id in node.goal_ids:
                if goal_id not in canonical_by_id:
                    issues.append(_error("node_unknown_goal_id", f"Node traces to unknown Goal {goal_id}.", node_id=node.node_id))
                    continue
                if auth[goal_id].status == "denied":
                    issues.append(_error("denied_goal_planned", f"Denied Goal {goal_id} must not execute.", node_id=node.node_id))
                if ready[goal_id].status.startswith("blocked_") or ready[goal_id].status == "satisfied_by_artifact":
                    issues.append(_error("non_executable_goal_planned", f"Blocked or satisfied Goal {goal_id} must not execute.", node_id=node.node_id))
                if source[goal_id].status not in {"requires_execution", "source_for_execution"}:
                    issues.append(_error("source_status_not_executable", f"Goal {goal_id} source state is not executable.", node_id=node.node_id))
            _validate_runtime_inputs(node, issues, required=require_runtime_inputs)
            for tool in node.required_tools:
                if tool in GLOBAL_FORBIDDEN_TOOLS:
                    issues.append(_error("forbidden_tool_requested", f"Forbidden tool requested: {tool}", node_id=node.node_id, tool=tool))

        for tool in candidate_plan.allowed_tools:
            if tool in GLOBAL_FORBIDDEN_TOOLS:
                issues.append(_error("forbidden_tool_requested", f"Forbidden tool requested: {tool}", tool=tool))
        if _has_cycle(candidate_plan):
            issues.append(_error("dependency_cycle", "Plan dependencies must be acyclic."))

        status: ValidationStatus = "blocked" if any(item.severity == "error" for item in issues) else "validated"
        return PlanValidationResult(
            candidate_plan=candidate_plan,
            validated_plan=candidate_plan.model_copy(deep=True),
            status=status,
            issues=issues,
            authorization={"goals": [item.model_dump(mode="json") for item in authorization]},
            approval_requirements=list(candidate_plan.approval_requirements),
            execution_mode="draft_only" if any(node.node_type == "workorder" for node in candidate_plan.nodes) else "normal",
            post_execution_confirmation_required=any(node.node_type == "workorder" for node in candidate_plan.nodes),
        )


def _validate_runtime_inputs(node, issues: list[PlanValidationIssue], *, required: bool) -> None:
    if not required:
        return
    inputs = node.inputs
    if node.node_type == "sql" and not str(inputs.get("sql_query") or "").strip():
        issues.append(_error("missing_sql_query", "SQL node requires sql_query before runtime.", node_id=node.node_id))
    if node.node_type == "rag" and not str(inputs.get("query") or "").strip():
        issues.append(_error("missing_rag_query", "RAG node requires query before runtime.", node_id=node.node_id))
    if node.node_type == "report" and not any(str(inputs.get(key) or "").strip() for key in ("operation_report_payload", "target_artifact_id")):
        issues.append(_error("missing_report_source", "Report node requires a canonical source.", node_id=node.node_id))


def _has_cycle(plan: ExecutionPlan) -> bool:
    graph: dict[str, list[str]] = {node.node_id: [] for node in plan.nodes}
    for edge in plan.edges:
        graph.setdefault(edge.source, []).append(edge.target)
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(visit(target) for target in graph.get(node_id, [])):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False
    return any(visit(node_id) for node_id in graph)


def _error(code: str, message: str, *, node_id: str | None = None, tool: str | None = None) -> PlanValidationIssue:
    return PlanValidationIssue(code=code, severity="error", message=message, node_id=node_id, tool=tool)
