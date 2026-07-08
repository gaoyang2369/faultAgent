"""Skill-level execution readiness and V2 stream helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..diagnosis.contracts import DiagnosisRequest
from ..diagnosis.report_mapper import map_artifact_to_report_payload
from ..diagnosis.artifact_store import get_thread_artifact
from ..single_agent.sql_safety import build_fallback_sql_query
from .contracts import ExecutionPlan, PlanSnapshotV2
from .flags import AgentEngineFlags, effective_skill_mode


@dataclass(frozen=True)
class V2ExecutionDecision:
    execute_v2: bool
    skill_name: str
    effective_mode: str
    plan: ExecutionPlan
    fallback_reason: str = ""


def decide_v2_execution(
    *,
    snapshot: PlanSnapshotV2,
    thread_id: str,
    flags: AgentEngineFlags | None = None,
) -> V2ExecutionDecision:
    skill_name = snapshot.skill_route.primary_skill or "clarification"
    mode = effective_skill_mode(skill_name, flags=flags)
    prepared = _prepare_plan(snapshot.execution_plan, snapshot=snapshot, thread_id=thread_id)
    if mode != "v2":
        return V2ExecutionDecision(False, skill_name, mode, prepared, f"skill_mode:{mode}")
    reason = _readiness_blocker(skill_name, prepared, snapshot=snapshot)
    if reason:
        return V2ExecutionDecision(False, skill_name, mode, prepared, reason)
    return V2ExecutionDecision(True, skill_name, mode, prepared)


def _prepare_plan(plan: ExecutionPlan, *, snapshot: PlanSnapshotV2, thread_id: str) -> ExecutionPlan:
    prepared = plan.model_copy(deep=True)
    for node in prepared.nodes:
        node_type = str(node.get("node_type") or "")
        inputs = dict(node.get("inputs") or {})
        if node_type == "rag":
            query = _rag_query(snapshot)
            if query:
                inputs.setdefault("query", query)
        elif node_type == "sql":
            request = _diagnosis_request(snapshot)
            query = build_fallback_sql_query(request, asset_filters=list(snapshot.intent_frame.device_refs))
            inputs.setdefault("sql_query", query)
            inputs.setdefault("use_checker", False)
            inputs.setdefault("equipment_hint", request.equipment_hint or "")
            inputs.setdefault("fault_code_hint", request.fault_code_hint or "")
        elif node_type == "report":
            reportable = _reportable_payload(thread_id)
            if reportable:
                inputs.update(
                    {
                        "title": reportable.get("title") or "DCMA 运行诊断报告",
                        "chart_payload": reportable.get("chart_payload") or "",
                        "operation_report_payload": reportable.get("operation_report_payload") or "",
                        "report_filename": reportable.get("report_filename") or "",
                    }
                )
        if inputs:
            node["inputs"] = inputs
    return prepared


def _readiness_blocker(skill_name: str, plan: ExecutionPlan, *, snapshot: PlanSnapshotV2) -> str:
    node_types = [str(node.get("node_type") or "") for node in plan.nodes]
    if skill_name == "fault_code_explain":
        has_query = any(str((node.get("inputs") or {}).get("query") or "").strip() for node in plan.nodes if node.get("node_type") == "rag")
        return "" if "rag" in node_types and has_query else "fault_code_explain_missing_rag_query"
    if skill_name == "runtime_status":
        has_device = bool(snapshot.intent_frame.device_refs)
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
    if skill_name in {"alarm_triage", "root_cause", "workorder_decision"}:
        return f"{skill_name}_v2_execution_not_enabled_in_phase9"
    return "skill_not_enabled_for_v2_execution"


def _rag_query(snapshot: PlanSnapshotV2) -> str:
    queries = [item for item in snapshot.rewrite_frame.retrieval_queries if str(item).strip()]
    if queries:
        return str(queries[0])
    codes = [item for item in snapshot.intent_frame.fault_code_refs if str(item).strip()]
    if codes:
        return " ".join(codes)
    return snapshot.rewrite_frame.user_rewrite or snapshot.intent_frame.normalized_message


def _diagnosis_request(snapshot: PlanSnapshotV2) -> DiagnosisRequest:
    devices = list(snapshot.intent_frame.device_refs)
    codes = list(snapshot.intent_frame.fault_code_refs)
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


def _reportable_payload(thread_id: str) -> dict[str, Any]:
    if not thread_id:
        return {}
    artifact = get_thread_artifact(thread_id)
    if artifact is None:
        return {}
    try:
        return map_artifact_to_report_payload(artifact)
    except Exception:
        return {}
