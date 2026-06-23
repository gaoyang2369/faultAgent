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
from ...runtime.diagnosis_contract_adapter import build_diagnosis_contract_payload
from ..contracts import AgentTrace, SingleAgentDecision
from ..reporting import extract_report_url

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

    return {
        "type": "chat_complete",
        "thread_id": thread_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "runtime": RUNTIME_NAME,
        "final_content": final_answer,
        "report_filename": None,
        "report_url": None,
        "decision": decision.model_dump(),
        "authorization": decision.authorization,
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

    return {
        "type": "chat_complete",
        "thread_id": thread_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "runtime": RUNTIME_NAME,
        "final_content": final_answer,
        "report_filename": report_artifact.report_filename,
        "report_url": extract_report_url(report_artifact.save_result),
        "decision": decision.model_dump(),
        "authorization": decision.authorization,
        "todos": todos,
        "workflow_route": {
            "primary_task_type": decision.primary_task_type,
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

    complete_payload = {
        "type": "chat_complete",
        "thread_id": thread_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "runtime": RUNTIME_NAME,
        "final_content": final_answer,
        "report_filename": report_artifact.report_filename,
        "report_url": extract_report_url(report_artifact.save_result),
        "decision": decision.model_dump(),
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
        "evidence_bundle": evidence_bundle.model_dump(exclude_none=True) if evidence_bundle else None,
        "output_guardrail": output_guardrail,
        "rendered_answer": (
            rendered_answer.model_dump(exclude_none=True)
            if hasattr(rendered_answer, "model_dump")
            else rendered_answer
        ),
        "workflow_route": {
            "primary_task_type": decision.primary_task_type,
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


def _merge_missing_contract_fields(
    payload: dict[str, Any],
    contract_payload: dict[str, Any],
) -> None:
    for key, value in contract_payload.items():
        if key not in payload or payload.get(key) in (None, [], {}):
            payload[key] = value
