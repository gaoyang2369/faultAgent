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

from ..contracts import ArtifactRoleBinding, ExecutionPlan
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
            _validate_role_bindings(node, candidate_plan, issues)
            for tool in node.required_tools:
                if tool in GLOBAL_FORBIDDEN_TOOLS:
                    issues.append(_error("forbidden_tool_requested", f"Forbidden tool requested: {tool}", node_id=node.node_id, tool=tool))

        for tool in candidate_plan.allowed_tools:
            if tool in GLOBAL_FORBIDDEN_TOOLS:
                issues.append(_error("forbidden_tool_requested", f"Forbidden tool requested: {tool}", tool=tool))
        _validate_planned_outputs(candidate_plan, issues)
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
            execution_mode="draft_only" if any(node.node_type == "workorder" and node.inputs.get("create_draft") for node in candidate_plan.nodes) else "normal",
            post_execution_confirmation_required=any(node.node_type == "workorder" and node.inputs.get("create_draft") for node in candidate_plan.nodes),
        )


def _validate_runtime_inputs(node, issues: list[PlanValidationIssue], *, required: bool) -> None:
    if not required:
        return
    inputs = node.inputs
    if str(inputs.get("preparation_error") or ""):
        issues.append(
            _error(
                str(inputs["preparation_error"]),
                "Runtime input preparation failed before node execution.",
                node_id=node.node_id,
            )
        )
    if node.node_type == "sql" and not str(inputs.get("sql_query") or "").strip():
        issues.append(_error("missing_sql_query", "SQL node requires sql_query before runtime.", node_id=node.node_id))
    if node.node_type == "rag" and not str(inputs.get("query") or "").strip():
        issues.append(_error("missing_rag_query", "RAG node requires query before runtime.", node_id=node.node_id))


def _validate_role_bindings(node, plan: ExecutionPlan, issues: list[PlanValidationIssue]) -> None:
    inputs = node.inputs
    if str(inputs.get("goal_id") or "") != node.goal_id or str(inputs.get("node_id") or "") != node.node_id:
        issues.append(_error("node_input_identity_mismatch", "Runtime input must preserve its exact goal_id and node_id.", node_id=node.node_id))
    bindings: list[ArtifactRoleBinding] = []
    for raw in inputs.get("artifact_role_bindings", []) or []:
        try:
            binding = ArtifactRoleBinding.model_validate(raw)
        except Exception:
            issues.append(_error("invalid_artifact_role_binding", "Artifact role binding is invalid.", node_id=node.node_id))
            continue
        bindings.append(binding)
        if binding.goal_id != node.goal_id or binding.node_id != node.node_id:
            issues.append(_error("artifact_binding_scope_mismatch", "Artifact binding must belong to the runtime node Goal.", node_id=node.node_id))
        if not binding.artifact_id or not binding.artifact_type:
            issues.append(_error("artifact_binding_identity_missing", "Artifact binding requires an exact ID and type.", node_id=node.node_id))
        expected_type = {
            "runtime_sql_source": "sql_artifact",
            "knowledge_source": "knowledge_artifact",
            "comparison_member": "sql_artifact",
            "analysis_source": "analysis_artifact",
            "report_source": "analysis_artifact",
            "tabular_source": "sql_artifact",
        }.get(binding.role)
        if binding.role == "workorder_source":
            if binding.artifact_type not in {"analysis_artifact", "report_artifact"}:
                issues.append(_error("artifact_role_type_mismatch", "Workorder source must be Analysis or Report.", node_id=node.node_id))
        elif expected_type and binding.artifact_type != expected_type:
            issues.append(_error("artifact_role_type_mismatch", f"{binding.role} requires {expected_type}.", node_id=node.node_id))
        if binding.producer_node_id:
            producer = next((item for item in plan.nodes if item.node_id == binding.producer_node_id), None)
            dependency = any(edge.source == binding.producer_node_id and edge.target == node.node_id for edge in plan.edges)
            if producer is None or not dependency or producer.planned_output_artifact_id != binding.artifact_id:
                issues.append(_error("artifact_binding_not_from_dependency", "Planned input Artifact must come from a real DAG dependency.", node_id=node.node_id))

    counts = {role: sum(item.role == role for item in bindings) for role in {
        "runtime_sql_source", "knowledge_source", "comparison_member", "report_source", "tabular_source", "workorder_source"
    }}
    if node.node_type == "sql" and bindings:
        issues.append(_error("sql_artifact_input_forbidden", "SQL nodes accept zero Artifact inputs.", node_id=node.node_id))
    elif node.node_type == "analysis":
        if counts["runtime_sql_source"] != 1:
            issues.append(_error("analysis_sql_source_cardinality", "Analysis requires exactly one runtime_sql_source.", node_id=node.node_id))
        if counts["knowledge_source"] > 1:
            issues.append(_error("analysis_knowledge_source_cardinality", "Analysis accepts at most one knowledge_source.", node_id=node.node_id))
    elif node.node_type == "comparison":
        members = [item for item in bindings if item.role == "comparison_member"]
        if len(members) < 2:
            issues.append(_error("comparison_member_cardinality", "Comparison requires at least two comparison_member bindings.", node_id=node.node_id))
        devices = [item.device_ref for item in members]
        orders = [item.member_order for item in members]
        if any(not item for item in devices) or len(set(devices)) != len(devices):
            issues.append(_error("comparison_member_device_mapping", "Every comparison member requires one unique device identity.", node_id=node.node_id))
        if any(item is None for item in orders) or orders != list(range(len(members))):
            issues.append(_error("comparison_member_order", "Comparison member order must be explicit and stable.", node_id=node.node_id))
    elif node.node_type == "report":
        if counts["report_source"] != 1:
            issues.append(_error("report_source_cardinality", "Report requires exactly one report_source.", node_id=node.node_id))
        if counts["tabular_source"] > 1:
            issues.append(_error("report_tabular_source_cardinality", "Report accepts at most one tabular_source.", node_id=node.node_id))
    elif node.node_type == "workorder" and counts["workorder_source"] != 1 and node.inputs.get("action_type") != "evaluate_workorder_need":
        issues.append(_error("workorder_source_cardinality", "Workorder requires exactly one workorder_source.", node_id=node.node_id))


def _validate_planned_outputs(plan: ExecutionPlan, issues: list[PlanValidationIssue]) -> None:
    producing = {"sql", "rag", "analysis", "comparison", "report", "workorder"}
    ids = [node.planned_output_artifact_id for node in plan.nodes if node.node_type in producing]
    if any(not artifact_id for artifact_id in ids) or len(ids) != len(set(ids)):
        issues.append(_error("planned_output_artifact_identity", "Every producing node requires one unique planned output Artifact ID."))


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
