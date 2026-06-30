"""Completion payload builders for the restricted single-agent runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    EvidenceBundle,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ...context import summarize_resolved_context
from ...runtime.diagnosis_contract_adapter import build_diagnosis_contract_payload
from ..contracts import AgentTrace, SingleAgentDecision
from ..reporting import extract_report_url
from ..workflow import summarize_goal_set

RUNTIME_NAME = "restricted_single_agent"


def build_direct_complete_payload(
    *,
    thread_id: str,
    trace_id: str,
    request_id: str,
    final_answer: str,
    decision: SingleAgentDecision,
    trace: AgentTrace,
    event_count: int,
) -> dict[str, Any]:
    """Build the complete payload for lightweight direct replies."""

    resolved_context = summarize_resolved_context(decision.resolved_context)
    goal_set = summarize_goal_set(decision.goal_set)
    return {
        "type": "chat_complete",
        "thread_id": thread_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "runtime": RUNTIME_NAME,
        "task_family": decision.task_family,
        "final_content": final_answer,
        "report_filename": None,
        "report_url": None,
        "decision": decision.model_dump(),
        "resolved_context": resolved_context,
        "goal_set": goal_set,
        "authorization": decision.authorization,
        "ui_payload": build_ui_payload(decision=decision),
        "trace": trace.model_dump(exclude_none=True),
        "todos": [],
        "event_count": event_count,
        "timestamp": datetime.now().isoformat(),
    }


def build_report_handoff_complete_payload(
    *,
    thread_id: str,
    trace_id: str,
    request_id: str,
    final_answer: str,
    report_artifact: ReportStepArtifact,
    decision: SingleAgentDecision,
    todos: list[dict[str, Any]],
    trace: AgentTrace,
    event_count: int,
) -> dict[str, Any]:
    """Build the complete payload for report generation from an existing artifact."""

    resolved_context = summarize_resolved_context(decision.resolved_context)
    goal_set = summarize_goal_set(decision.goal_set)
    return {
        "type": "chat_complete",
        "thread_id": thread_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "runtime": RUNTIME_NAME,
        "task_family": decision.task_family,
        "final_content": final_answer,
        "report_filename": report_artifact.report_filename,
        "report_url": extract_report_url(report_artifact.save_result),
        "decision": decision.model_dump(),
        "resolved_context": resolved_context,
        "goal_set": goal_set,
        "authorization": decision.authorization,
        "ui_payload": build_ui_payload(decision=decision, report_artifact=report_artifact),
        "todos": todos,
        "workflow_route": {
            "primary_task_type": decision.primary_task_type,
            "task_family": decision.task_family,
            "task_family_reason": decision.task_family_reason,
            "task_family_source": decision.task_family_source,
            "task_family_warnings": decision.task_family_warnings,
            "candidate_task_types": decision.candidate_task_types,
            "intent_stack": decision.intent_stack,
            "goal_set": goal_set,
            "resolved_context": resolved_context,
            "context_resolution": decision.context_resolution,
            "active_case_id": decision.active_case_id,
            "relation_to_previous": decision.relation_to_previous,
            "plan_mode": decision.plan_mode,
            "evidence_mode": decision.evidence_mode,
            "referenced_artifact_id": decision.referenced_artifact_id,
            "referenced_case_id": decision.referenced_case_id,
            "required_evidence": decision.required_evidence,
            "satisfied_evidence": decision.satisfied_evidence,
            "missing_or_stale_evidence": decision.missing_or_stale_evidence,
            "should_refresh_runtime_data": decision.should_refresh_runtime_data,
            "action_target": decision.action_target,
            "subgoals": decision.subgoals,
            "missing_slots": decision.missing_slots,
        },
        "workflow_policy": decision.workflow_policy,
        "trace": trace.model_dump(exclude_none=True),
        "event_count": event_count,
        "timestamp": datetime.now().isoformat(),
    }


def build_diagnosis_complete_payload(
    *,
    thread_id: str,
    trace_id: str,
    request_id: str,
    final_answer: str,
    decision: SingleAgentDecision,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    permission_check_result: dict[str, Any],
    risk_check_result: dict[str, Any],
    resolution_recommendation: dict[str, Any],
    audit_log_result: dict[str, Any],
    workorder_suggestion: WorkOrderSuggestion,
    report_artifact: ReportStepArtifact,
    evidence_bundle: EvidenceBundle | None,
    output_guardrail: dict[str, Any],
    rendered_answer: Any | None = None,
    saved_envelope: DiagnosisArtifactEnvelope,
    trace: AgentTrace,
    todos: list[dict[str, Any]],
    event_count: int,
) -> dict[str, Any]:
    """Build the full complete payload for diagnosis workflows."""

    resolved_context = summarize_resolved_context(decision.resolved_context)
    goal_set = summarize_goal_set(decision.goal_set)
    complete_payload = {
        "type": "chat_complete",
        "thread_id": thread_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "runtime": RUNTIME_NAME,
        "task_family": decision.task_family,
        "final_content": final_answer,
        "report_filename": report_artifact.report_filename,
        "report_url": extract_report_url(report_artifact.save_result),
        "decision": decision.model_dump(),
        "resolved_context": resolved_context,
        "goal_set": goal_set,
        "authorization": decision.authorization,
        "sql_artifact": sql_artifact.model_dump(exclude_none=True),
        "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
        "analysis_artifact": analysis_artifact.model_dump(exclude_none=True),
        "permission_check": permission_check_result,
        "risk_check": risk_check_result,
        "resolution_recommendation": resolution_recommendation,
        "audit_log": audit_log_result,
        "workorder_decision": workorder_suggestion.model_dump(exclude_none=True),
        "report_artifact": report_artifact.model_dump(exclude_none=True),
        "ui_payload": build_ui_payload(
            decision=decision,
            sql_artifact=sql_artifact,
            analysis_artifact=analysis_artifact,
            report_artifact=report_artifact,
        ),
        "evidence_bundle": evidence_bundle.model_dump(exclude_none=True) if evidence_bundle else None,
        "output_guardrail": output_guardrail,
        "rendered_answer": (
            rendered_answer.model_dump(exclude_none=True)
            if hasattr(rendered_answer, "model_dump")
            else rendered_answer
        ),
        "workflow_route": {
            "primary_task_type": decision.primary_task_type,
            "task_family": decision.task_family,
            "task_family_reason": decision.task_family_reason,
            "task_family_source": decision.task_family_source,
            "task_family_warnings": decision.task_family_warnings,
            "candidate_task_types": decision.candidate_task_types,
            "intent_stack": decision.intent_stack,
            "goal_set": goal_set,
            "resolved_context": resolved_context,
            "context_resolution": decision.context_resolution,
            "active_case_id": decision.active_case_id,
            "relation_to_previous": decision.relation_to_previous,
            "plan_mode": decision.plan_mode,
            "evidence_mode": decision.evidence_mode,
            "referenced_artifact_id": decision.referenced_artifact_id,
            "referenced_case_id": decision.referenced_case_id,
            "required_evidence": decision.required_evidence,
            "satisfied_evidence": decision.satisfied_evidence,
            "missing_or_stale_evidence": decision.missing_or_stale_evidence,
            "should_refresh_runtime_data": decision.should_refresh_runtime_data,
            "action_target": decision.action_target,
            "route_confidence": decision.route_confidence,
            "objects": decision.objects,
            "time_window": decision.time_window,
            "subgoals": decision.subgoals,
            "missing_slots": decision.missing_slots,
            "risk_level": decision.risk_level,
            "requested_output": decision.requested_output,
        },
        "workflow_policy": decision.workflow_policy,
        "todos": todos,
        "artifact": saved_envelope.model_dump(exclude_none=True),
        "trace": trace.model_dump(exclude_none=True),
        "event_count": event_count,
        "timestamp": datetime.now().isoformat(),
    }
    _merge_missing_contract_fields(
        complete_payload,
        build_diagnosis_contract_payload(saved_envelope),
    )
    return complete_payload


def build_ui_payload(
    *,
    decision: SingleAgentDecision,
    sql_artifact: SqlStepArtifact | None = None,
    analysis_artifact: AnalysisStepArtifact | None = None,
    report_artifact: ReportStepArtifact | None = None,
) -> dict[str, Any]:
    """Return a small presentation hint that prevents the frontend from guessing."""

    task_type = str(decision.primary_task_type or "")
    authorization = decision.authorization or {}
    auth_mode = str(authorization.get("mode") or "")
    data_state = str(getattr(sql_artifact, "data_state", "") or "")
    ui_type = "text_only"
    denied_reason_code = str(authorization.get("denied_reason_code") or "")
    if task_type == "permission_scope_query":
        ui_type = "permission_scope"
    elif task_type == "report_generation" and (
        denied_reason_code == "report_permission_denied"
        or (
            auth_mode == "deny"
            and isinstance(authorization.get("denied_nodes"), dict)
            and authorization["denied_nodes"].get("report") == "missing_report_permission"
        )
    ):
        ui_type = "report_blocked"
    elif auth_mode in {"deny", "clarify"}:
        ui_type = "access_denied"
    elif task_type == "knowledge_qa":
        ui_type = "knowledge_card"
    elif task_type == "report_generation":
        ui_type = "report_status"
    elif data_state in {"out_of_scope", "blocked", "empty"} or auth_mode == "degrade":
        ui_type = "text_only"
    elif task_type == "status_query":
        ui_type = "status_card"
    elif task_type in {"alarm_triage", "fault_diagnosis", "root_cause_analysis", "health_assessment"}:
        ui_type = "diagnosis_card"

    objects = decision.objects or {}
    devices = [str(item).strip() for item in objects.get("device_ids", []) if str(item).strip()]
    alarm_codes = [str(item).strip() for item in objects.get("alarm_codes", []) if str(item).strip()]
    return {
        "type": ui_type,
        "task_type": task_type,
        "data_state": data_state or None,
        "device_label": devices[0] if devices else None,
        "fault_code": alarm_codes[0] if alarm_codes else None,
        "confidence": getattr(analysis_artifact, "confidence", None),
        "report_generated": bool(report_artifact and report_artifact.success),
    }


def _merge_missing_contract_fields(
    payload: dict[str, Any],
    contract_payload: dict[str, Any],
) -> None:
    for key, value in contract_payload.items():
        if key not in payload or payload.get(key) in (None, [], {}):
            payload[key] = value
