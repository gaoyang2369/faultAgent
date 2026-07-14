"""Prepare canonical plans without consulting legacy request authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fault_diagnosis.agent.context.artifact_access import resolve_target_artifact
from fault_diagnosis.domain.artifacts import AnalysisArtifactPayload
from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)
from fault_diagnosis.domain.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.domain.security.sql_safety import SourceTableResolutionError, build_fallback_sql_query

from ..contracts import (
    AnalysisNodeInputs,
    ApprovalNodeInputs,
    ArtifactRoleBinding,
    ComparisonNodeInputs,
    ExecutionPlan,
    PlanSnapshotV2,
    RagNodeInputs,
    ReportNodeInputs,
    SqlNodeInputs,
    WorkorderNodeInputs,
)
from ..planning import PlanValidationResult, PlanValidator


@dataclass(frozen=True)
class V2ExecutionDecision:
    execute_v2: bool
    skill_name: str
    effective_mode: str
    plan: ExecutionPlan
    fallback_reason: str = ""


def prepare_v2_execution_plan(
    *, snapshot: PlanSnapshotV2, thread_id: str, auth_context: Any | None = None
) -> ExecutionPlan:
    return prepare_v2_execution_validation(
        snapshot=snapshot,
        thread_id=thread_id,
        auth_context=auth_context,
    ).validated_plan


def prepare_v2_execution_validation(
    *, snapshot: PlanSnapshotV2, thread_id: str, auth_context: Any | None = None
) -> PlanValidationResult:
    request = CanonicalTurnRequest.model_validate(snapshot.metadata["canonical_request"])
    prepared = _prepare_plan(
        snapshot.execution_plan,
        request=request,
        thread_id=thread_id,
        auth_context=auth_context,
    )
    return PlanValidator().validate(
        candidate_plan=prepared,
        request=request,
        authorization=[GoalAuthorizationDecision.model_validate(item) for item in snapshot.metadata["goal_authorization"]],
        readiness=[GoalReadinessDecision.model_validate(item) for item in snapshot.metadata["goal_readiness"]],
        sources=[GoalSourceResolution.model_validate(item) for item in snapshot.metadata["goal_source_resolution"]],
        require_runtime_inputs=True,
    )


def decide_v2_execution(
    *,
    snapshot: PlanSnapshotV2,
    thread_id: str,
    flags: Any | None = None,  # noqa: ARG001 - public compatibility argument.
) -> V2ExecutionDecision:
    prepared = prepare_v2_execution_plan(snapshot=snapshot, thread_id=thread_id)
    skill_name = next((node.skill for node in prepared.nodes if node.skill), "clarification")
    return V2ExecutionDecision(True, skill_name, "v2", prepared, _readiness_blocker(prepared))


def _prepare_plan(
    plan: ExecutionPlan,
    *,
    request: CanonicalTurnRequest,
    thread_id: str,
    auth_context: Any | None,
) -> ExecutionPlan:
    prepared = plan.model_copy(deep=True)
    auth = auth_context or build_auth_context(user_id=request.user_id, role="guest")
    goals = {goal.goal_id: goal for goal in request.goals}
    for node in prepared.nodes:
        inputs = dict(node.inputs)
        goal = goals.get(node.goal_id)
        if goal is None:
            inputs["preparation_error"] = "canonical_goal_not_found"
        elif node.node_type == "rag":
            inputs["query"] = _rag_query(request, goal, inputs)
            codes = _texts(inputs.get("fault_code_refs")) or _fault_codes(goal)
            inputs.setdefault("retrieval_strategy", "fault_code_exact_then_semantic" if codes else "semantic_search")
            inputs.setdefault("top_k", 5 if codes else 3)
        elif node.node_type == "sql":
            diagnosis_request = _diagnosis_request(request, goal, devices=_node_devices(inputs), slots=inputs.get("canonical_resolved_slots") or {})
            try:
                query = build_fallback_sql_query(diagnosis_request, asset_filters=_node_devices(inputs))
            except SourceTableResolutionError as exc:
                inputs["preparation_error"] = exc.code
            else:
                inputs["sql_query"] = query
                inputs.setdefault("use_checker", False)
                inputs["equipment_hint"] = diagnosis_request.equipment_hint or ""
                inputs["fault_code_hint"] = diagnosis_request.fault_code_hint or ""
        access_error = _verify_external_bindings(
            node=node,
            inputs=inputs,
            thread_id=thread_id,
            auth_context=auth,
        )
        if access_error:
            inputs["preparation_error"] = access_error
        input_model = {
            "sql": SqlNodeInputs,
            "rag": RagNodeInputs,
            "analysis": AnalysisNodeInputs,
            "comparison": ComparisonNodeInputs,
            "report": ReportNodeInputs,
            "workorder": WorkorderNodeInputs,
            "approval": ApprovalNodeInputs,
        }.get(node.node_type)
        bindings = [ArtifactRoleBinding.model_validate(item) for item in inputs.get("artifact_role_bindings", [])]
        if input_model is not None and "source_artifact_refs" in input_model.model_fields:
            inputs["source_artifact_refs"] = [
                {"artifact_id": item.artifact_id, "artifact_type": item.artifact_type}
                for item in bindings
            ]
        node.inputs = (
            input_model.model_validate(inputs).model_dump(mode="json")
            if input_model is not None
            else inputs
        )
    return prepared


def _verify_external_bindings(*, node, inputs: dict[str, Any], thread_id: str, auth_context: Any) -> str:
    bindings = [ArtifactRoleBinding.model_validate(item) for item in inputs.get("artifact_role_bindings", [])]
    loaded: dict[str, Any] = {}
    for binding in bindings:
        if binding.producer_node_id:
            continue
        expected_devices = [binding.device_ref] if binding.device_ref else _node_devices(inputs)
        access = resolve_target_artifact(
            thread_id=thread_id,
            artifact_id=binding.artifact_id,
            auth=auth_context,
            expected_types={binding.artifact_type},
            expected_devices=expected_devices if len(expected_devices) == 1 else [],
            require_complete_lineage=True,
        )
        if not access.allowed or access.record is None or access.record.artifact_envelope is None:
            return access.code
        loaded[binding.role] = access.record.artifact_envelope
    if node.node_type == "report" and "report_source" in loaded:
        source = loaded["report_source"]
        if not isinstance(source.payload, AnalysisArtifactPayload) or source.payload.report_input_snapshot is None:
            return "legacy_analysis_missing_report_input_snapshot"
        tabular = next((item for item in bindings if item.role == "tabular_source"), None)
        expected_tabular = source.payload.report_input_snapshot.tabular_source_sql_artifact_id
        if (tabular.artifact_id if tabular is not None else None) != expected_tabular:
            return "report_tabular_source_snapshot_mismatch"
    return ""


def _diagnosis_request(
    request: CanonicalTurnRequest,
    goal: CanonicalGoal,
    *,
    devices: list[str],
    slots: dict[str, Any],
) -> DiagnosisRequest:
    codes = _texts(slots.get("fault_code")) or _fault_codes(goal) or [
        entity.value for entity in request.current_parse.entities if entity.kind == "fault_code"
    ]
    window = slots.get("time_window") or goal.resolved_slots.get("time_window")
    return DiagnosisRequest(
        user_message=request.raw_message,
        user_identity=request.user_id,
        equipment_hint=devices[0] if devices else None,
        fault_code_hint=codes[0] if codes else None,
        metric_hint=None,
        time_range_hint=str(window) if window else None,
        needs_report=goal.capability == "generate_report",
        report_format="html",
        analysis_goal=goal.capability,
    )


def _rag_query(request: CanonicalTurnRequest, goal: CanonicalGoal, inputs: dict[str, Any]) -> str:
    codes = _texts(inputs.get("fault_code_refs")) or _fault_codes(goal) or [
        entity.value for entity in request.current_parse.entities if entity.kind == "fault_code"
    ]
    detail = " 详细说明" if any(marker in request.raw_message for marker in ("详细", "展开", "字段")) else ""
    return f"{' '.join(codes)} 故障原因 触发条件 处理措施 检查步骤 复位方法{detail}" if codes else request.raw_message


def _fault_codes(goal: CanonicalGoal) -> list[str]:
    value = goal.resolved_slots.get("fault_code")
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if value else []


def _texts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if value else []


def _node_devices(inputs: dict[str, Any]) -> list[str]:
    return [str(item) for item in inputs.get("device_refs", []) if str(item)]


def _readiness_blocker(plan: ExecutionPlan) -> str:
    for node in plan.nodes:
        error = str(node.inputs.get("preparation_error") or "")
        if error:
            return error
        if node.node_type == "sql" and not str(node.inputs.get("sql_query") or "").strip():
            return "runtime_status_missing_sql_query"
        if node.node_type == "rag" and not str(node.inputs.get("query") or "").strip():
            return "fault_code_explain_missing_rag_query"
    return ""
