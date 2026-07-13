"""V2 runtime plan preparation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.domain.diagnosis.report_mapper import map_artifact_to_report_payload
from fault_diagnosis.domain.security.sql_safety import build_fallback_sql_query
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import get_thread_artifact
from fault_diagnosis.domain.security.permissions import build_auth_context
from ..context.artifact_access import resolve_target_artifact

from ..contracts import ExecutionPlan, PlanSnapshotV2
from ..planning import PlanValidationResult, PlanValidator


@dataclass(frozen=True)
class V2ExecutionDecision:
    execute_v2: bool
    skill_name: str
    effective_mode: str
    plan: ExecutionPlan
    fallback_reason: str = ""


def prepare_v2_execution_plan(*, snapshot: PlanSnapshotV2, thread_id: str, auth_context: Any | None = None) -> ExecutionPlan:
    return prepare_v2_execution_validation(
        snapshot=snapshot,
        thread_id=thread_id,
        auth_context=auth_context,
    ).validated_plan


def prepare_v2_execution_validation(
    *,
    snapshot: PlanSnapshotV2,
    thread_id: str,
    auth_context: Any | None = None,
) -> PlanValidationResult:
    prepared = _prepare_plan(snapshot.execution_plan, snapshot=snapshot, thread_id=thread_id, auth_context=auth_context)
    return PlanValidator().validate(
        candidate_plan=prepared,
        skill_route=snapshot.skill_route,
        intent_frame=snapshot.intent_frame,
        auth_context=auth_context,
        require_runtime_inputs=True,
    )


def decide_v2_execution(
    *,
    snapshot: PlanSnapshotV2,
    thread_id: str,
    flags: Any | None = None,  # noqa: ARG001 - retained for offline eval compatibility.
) -> V2ExecutionDecision:
    skill_name = snapshot.skill_route.primary_skill or "clarification"
    prepared = prepare_v2_execution_plan(snapshot=snapshot, thread_id=thread_id)
    reason = _readiness_blocker(skill_name, prepared, snapshot=snapshot)
    return V2ExecutionDecision(True, skill_name, "v2", prepared, reason)


def _prepare_plan(plan: ExecutionPlan, *, snapshot: PlanSnapshotV2, thread_id: str, auth_context: Any | None = None) -> ExecutionPlan:
    prepared = plan.model_copy(deep=True)
    auth = auth_context or build_auth_context(role="guest")
    planned_node_types = {str(node.get("node_type") or "") for node in prepared.nodes}
    for node in prepared.nodes:
        node_type = str(node.get("node_type") or "")
        inputs = dict(node.get("inputs") or {})
        if node_type == "rag":
            query = _rag_query(snapshot, node)
            if query:
                inputs["query"] = query
            inputs.setdefault("retrieval_strategy", _rag_retrieval_strategy(snapshot))
            if snapshot.effective_request_frame.requested_output_mode == "detailed":
                inputs["top_k"] = max(int(inputs.get("top_k") or 1), 5)
            refs = _rag_source_artifact_refs(snapshot)
            if refs:
                inputs.setdefault("source_artifact_refs", refs)
        elif node_type == "sql":
            node_devices = [str(item) for item in inputs.get("device_refs", []) if str(item)]
            request = _diagnosis_request(snapshot, devices=node_devices)
            query = build_fallback_sql_query(request, asset_filters=node_devices)
            inputs.setdefault("sql_query", query)
            inputs.setdefault("use_checker", False)
            inputs.setdefault("equipment_hint", request.equipment_hint or "")
            inputs.setdefault("fault_code_hint", request.fault_code_hint or "")
        elif node_type == "report":
            reportable = _reportable_payload(
                thread_id,
                target_id=str(inputs.get("target_artifact_id") or snapshot.effective_request_frame.target_artifact_id or ""),
                auth_context=auth,
                expected_devices=_effective_devices(snapshot),
            )
            if reportable:
                inputs.update(
                    {
                        "title": reportable.get("title") or "DCMA 运行诊断报告",
                        "chart_payload": reportable.get("chart_payload") or "",
                        "operation_report_payload": reportable.get("operation_report_payload") or "",
                        "report_filename": reportable.get("report_filename") or "",
                    }
                )
            elif not inputs.get("operation_report_payload") and "analysis" in planned_node_types:
                inputs.setdefault("operation_report_payload", "__runtime_artifacts__")
        elif node_type == "analysis" and snapshot.effective_request_frame.target_artifact_id:
            access = resolve_target_artifact(
                thread_id=thread_id,
                artifact_id=snapshot.effective_request_frame.target_artifact_id,
                auth=auth,
                expected_types={"sql_artifact", "analysis_artifact", "structured_analysis_artifact"},
                expected_devices=_effective_devices(snapshot),
                require_complete_lineage=True,
            )
            if access.allowed and access.record is not None:
                inputs["source_artifact_id"] = snapshot.effective_request_frame.target_artifact_id
                inputs["source_artifact_type"] = str(access.record.manifest.get("artifact_type") or "")
                inputs["source_artifact_payload"] = access.record.payload
            else:
                inputs["artifact_access_error"] = access.code
        if node_type == "workorder":
            inputs.setdefault("create_draft", True)
            inputs.setdefault("manual_confirmation_required", True)
            inputs.setdefault("draft_only", True)
            inputs.update(_workorder_manifest_inputs(thread_id, snapshot=snapshot, inputs=inputs, auth_context=auth))
        if inputs:
            node["inputs"] = inputs
    return prepared


def _readiness_blocker(skill_name: str, plan: ExecutionPlan, *, snapshot: PlanSnapshotV2) -> str:
    node_types = [str(node.get("node_type") or "") for node in plan.nodes]
    if skill_name == "fault_code_explain":
        has_query = any(str((node.get("inputs") or {}).get("query") or "").strip() for node in plan.nodes if node.get("node_type") == "rag")
        return "" if "rag" in node_types and has_query else "fault_code_explain_missing_rag_query"
    if skill_name == "runtime_status":
        has_device = bool(_effective_devices(snapshot))
        has_sql = any(str((node.get("inputs") or {}).get("sql_query") or "").strip() for node in plan.nodes if node.get("node_type") == "sql")
        if not has_device:
            return "runtime_status_missing_device"
        return "" if "sql" in node_types and has_sql else "runtime_status_missing_sql_query"
    if skill_name == "report_generation":
        has_report_payload = any(
            str((node.get("inputs") or {}).get("operation_report_payload") or "").strip()
            for node in plan.nodes
            if node.get("node_type") == "report"
        )
        return "" if has_report_payload else "report_generation_missing_reportable_artifact"
    if skill_name in {"alarm_triage", "root_cause"}:
        has_device = bool(_effective_devices(snapshot))
        has_sql = any(str((node.get("inputs") or {}).get("sql_query") or "").strip() for node in plan.nodes if node.get("node_type") == "sql")
        if "sql" in node_types and not has_device:
            return f"{skill_name}_missing_device"
        return "" if "sql" not in node_types or has_sql else f"{skill_name}_missing_sql_query"
    if skill_name == "workorder_decision":
        return ""
    return ""


def _rag_query(snapshot: PlanSnapshotV2, node: Any | None = None) -> str:
    effective = snapshot.effective_request_frame
    query_spec_id = str((node.get("query_spec_id") if node is not None else "") or "")
    for spec in effective.goal_query_specs:
        if spec.query_spec_id == query_spec_id and spec.rag_query:
            return spec.rag_query
    codes = [item for item in _effective_fault_codes(snapshot) if str(item).strip()]
    if (
        effective.semantic_intent in {"expand_previous_answer", "show_manual_fields"}
        and len(codes) == 1
    ):
        return (
            f"{codes[0]} 故障原因 触发条件 处理措施 检查步骤 复位方法 详细说明"
        )
    queries = [item for item in snapshot.rewrite_frame.retrieval_queries if str(item).strip()]
    if queries:
        return str(queries[0])
    if codes:
        suffix = " 故障原因 处理措施 检查步骤 详细说明" if snapshot.effective_request_frame.requested_output_mode == "detailed" else ""
        return f"{' '.join(codes)}{suffix}".strip()
    return snapshot.rewrite_frame.user_rewrite or snapshot.intent_frame.normalized_message


def _rag_retrieval_strategy(snapshot: PlanSnapshotV2) -> str:
    if _effective_fault_codes(snapshot):
        return "fault_code_exact_then_semantic"
    return "semantic_search"


def _rag_source_artifact_refs(snapshot: PlanSnapshotV2) -> list[dict[str, str]]:
    target_id = snapshot.effective_request_frame.target_artifact_id
    target_type = snapshot.effective_request_frame.target_artifact_type
    if not target_id:
        return []
    return [{"artifact_id": target_id, "artifact_type": target_type or ""}]


def _diagnosis_request(snapshot: PlanSnapshotV2, *, devices: list[str] | None = None) -> DiagnosisRequest:
    devices = list(devices if devices is not None else _effective_devices(snapshot))
    codes = _effective_fault_codes(snapshot)
    return DiagnosisRequest(
        user_message=snapshot.intent_frame.raw_message or snapshot.intent_frame.normalized_message,
        user_identity="agent_engine_v2",
        equipment_hint=devices[0] if devices else None,
        fault_code_hint=codes[0] if codes else None,
        metric_hint=None,
        time_range_hint=str(snapshot.intent_frame.time_window or "") or None,
        needs_report="report" in snapshot.intent_frame.requested_outputs,
        report_format="html",
        analysis_goal=snapshot.rewrite_frame.user_rewrite or snapshot.intent_frame.normalized_message,
    )


def _effective_devices(snapshot: PlanSnapshotV2) -> list[str]:
    return list(snapshot.effective_request_frame.effective_device_refs or snapshot.intent_frame.device_refs)


def _effective_fault_codes(snapshot: PlanSnapshotV2) -> list[str]:
    return list(snapshot.effective_request_frame.effective_fault_code_refs or snapshot.intent_frame.fault_code_refs)


def _workorder_manifest_inputs(
    thread_id: str,
    *,
    snapshot: PlanSnapshotV2,
    inputs: dict[str, Any],
    auth_context: Any,
) -> dict[str, Any]:
    target_id = str(
        inputs.get("target_artifact_id")
        or snapshot.effective_request_frame.target_artifact_id
        or ""
    )
    selected: dict[str, Any] = {}
    if target_id:
        access = resolve_target_artifact(
            thread_id=thread_id,
            artifact_id=target_id,
            auth=auth_context,
            expected_types={"report_artifact", "analysis_artifact", "structured_analysis_artifact"},
            expected_devices=_effective_devices(snapshot),
            require_complete_lineage=True,
        )
        if not access.allowed or access.record is None:
            return {"artifact_access_error": access.code}
        selected = dict(access.record.manifest)
    refs = _source_artifact_refs(selected, target_id=target_id, target_type=str(inputs.get("target_artifact_type") or ""))
    result: dict[str, Any] = {}
    if refs:
        result["source_artifact_refs"] = refs
    if selected:
        result["selected_findings_summary"] = _as_text_list(selected.get("findings"))[:6]
        result["risk_level"] = str(selected.get("risk_level") or selected.get("severity") or "")
        result["diagnosis_summary"] = str(selected.get("diagnosis_summary") or "")
        result["evidence_freshness"] = str(selected.get("freshness") or "")
        result["report_url"] = str(selected.get("report_url") or selected.get("report_filename") or "")
        if str(selected.get("freshness") or "") == "stale":
            result["stale_refresh_required"] = True
            result["stale_evidence_disclosure_required"] = True
    if snapshot.effective_request_frame.stale_evidence_disclosure_required:
        result["stale_refresh_required"] = True
        result["stale_evidence_disclosure_required"] = True
    return result


def _source_artifact_refs(selected: dict[str, Any], *, target_id: str, target_type: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    artifact_id = str(selected.get("artifact_id") or target_id or "")
    artifact_type = str(selected.get("artifact_type") or target_type or "")
    if artifact_id:
        refs.append({"artifact_id": artifact_id, "artifact_type": artifact_type})
    for key, artifact_type in (
        ("linked_analysis_artifact_id", "analysis_artifact"),
        ("linked_sql_artifact_id", "sql_artifact"),
    ):
        value = str(selected.get(key) or "")
        if value:
            refs.append({"artifact_id": value, "artifact_type": artifact_type})
    return refs


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _reportable_payload(
    thread_id: str,
    *,
    target_id: str = "",
    auth_context: Any | None = None,
    expected_devices: list[str] | None = None,
) -> dict[str, Any]:
    if not thread_id:
        return {}
    if target_id:
        access = resolve_target_artifact(
            thread_id=thread_id,
            artifact_id=target_id,
            auth=auth_context or build_auth_context(role="guest"),
            expected_types={"sql_artifact", "analysis_artifact", "structured_analysis_artifact", "comparison_artifact", "report_artifact"},
            expected_devices=list(expected_devices or []),
            require_complete_lineage=True,
        )
        if not access.allowed or access.record is None:
            return {}
        artifact = access.record.envelope
    else:
        artifact = get_thread_artifact(thread_id)
    if artifact is None:
        return {}
    try:
        return map_artifact_to_report_payload(artifact)
    except Exception:
        return {}
