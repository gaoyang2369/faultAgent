"""Diagnosis artifact envelope builders for completed single-agent runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    DiagnosisRequest,
    EvidenceBundle,
    EvidenceItem,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderDraftArtifact,
    WorkOrderSuggestion,
)
from ..context import build_case_state_snapshot
from .contracts import AgentTrace, SingleAgentDecision
from .reporting.source_resolution import has_reportable_material
from .sql_result_parser import parse_sql_rows
from .workflow.axes import task_profile_for_compat


def build_diagnosis_artifact_envelope(
    *,
    thread_id: str,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion,
    report_artifact: ReportStepArtifact,
    final_answer: str,
    decision: SingleAgentDecision,
    trace: AgentTrace,
    evidence_bundle: EvidenceBundle | None = None,
    output_guardrail: dict[str, object] | None = None,
    rendered_answer: Any | None = None,
    workflow_artifacts: dict[str, object] | None = None,
    auth: dict[str, Any] | None = None,
    authorization: dict[str, Any] | None = None,
    workorder_draft: WorkOrderDraftArtifact | None = None,
) -> DiagnosisArtifactEnvelope:
    evidence = (
        evidence_bundle.evidence_items
        if evidence_bundle is not None and evidence_bundle.evidence_items
        else [
            EvidenceItem(
                source_type="sql",
                title="SQL 查询摘要",
                content=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
                importance="high" if sql_artifact.success else "low",
            ),
            EvidenceItem(
                source_type="knowledge_base",
                title="知识检索摘要",
                content=knowledge_artifact.raw_output or knowledge_artifact.error or "未执行知识检索",
                importance="medium" if knowledge_artifact.success else "low",
            ),
            EvidenceItem(
                source_type="analysis",
                title="诊断结论",
                content=analysis_artifact.conclusion,
                importance="high",
            ),
        ]
    )
    payload = {
        "runtime": "restricted_single_agent",
        "status": "completed",
        "task_family": decision.task_family,
        "request": request.model_dump(exclude_none=True),
        "decision": decision.model_dump(),
        "sql_artifact": sql_artifact.model_dump(exclude_none=True),
        "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
        "analysis_artifact": analysis_artifact.model_dump(exclude_none=True),
        "workorder_decision": workorder_suggestion.model_dump(exclude_none=True),
        "report_artifact": report_artifact.model_dump(exclude_none=True),
        "trace": trace.model_dump(exclude_none=True),
        "output_guardrail": output_guardrail or {},
        "rendered_answer": (
            rendered_answer.model_dump(exclude_none=True)
            if hasattr(rendered_answer, "model_dump")
            else rendered_answer or {}
        ),
        "workflow_artifacts": workflow_artifacts or {},
        "auth": auth or {},
        "authorization": authorization or {},
    }
    normalized_rows = parse_sql_rows(sql_artifact.raw_output or sql_artifact.result_preview)
    if normalized_rows:
        payload["normalized_rows"] = normalized_rows
    row_device_names = _row_values(normalized_rows, "device_name")
    row_inverter_names = _row_values(normalized_rows, "inverter_name")
    row_fault_codes = _row_values(normalized_rows, "fault_code", "alarm_code")
    sample_window = _sample_window_from_rows(normalized_rows)
    requested_device = request.equipment_hint or _first((decision.objects or {}).get("device_ids"))
    resolved_device_name = _first(row_device_names)
    device_aliases = [
        value
        for value in [requested_device, resolved_device_name, *row_device_names, *row_inverter_names]
        if str(value or "").strip()
    ]
    data_window = {
        **(decision.time_window or {}),
        **sample_window,
    }
    payload.update(
        {
            "device": requested_device or resolved_device_name,
            "asset_id": requested_device or resolved_device_name,
            "device_name": resolved_device_name,
            "device_aliases": list(dict.fromkeys(str(item).strip() for item in device_aliases if str(item).strip())),
            "source_table": sql_artifact.source_table,
            "sql_artifact_id": trace.trace_id,
            "analysis_artifact_id": trace.trace_id,
            "evidence_bundle_id": evidence_bundle.bundle_id if evidence_bundle is not None else None,
            "row_count": sql_artifact.row_count,
            "fault_codes": _artifact_fault_codes(request, decision, row_fault_codes),
            "data_window": data_window,
            "latest_sample_time": sample_window.get("latest_sample_time"),
            "freshness": _freshness_from_sql(sql_artifact),
        }
    )
    if workorder_draft is not None:
        payload["workorder_draft"] = workorder_draft.model_dump(exclude_none=True)
    if evidence_bundle is not None:
        payload["evidence_bundle"] = evidence_bundle.model_dump(exclude_none=True)
    payload["reportable"] = has_reportable_material(payload)
    if not payload["reportable"]:
        payload["report_blockers"] = ["artifact_missing_reportable_material"]
    envelope = DiagnosisArtifactEnvelope(
        workflow_type=_artifact_type_from_decision(decision),
        thread_id=thread_id,
        created_at=datetime.now().isoformat(),
        request_summary=request.analysis_goal or request.user_message,
        final_answer=final_answer,
        report_filename=report_artifact.report_filename,
        payload=payload,
        evidence=evidence,
    )
    envelope.payload["case_state_snapshot"] = build_case_state_snapshot(envelope)
    return envelope


def _artifact_type_from_decision(decision: SingleAgentDecision) -> DiagnosisArtifactType:
    try:
        return DiagnosisArtifactType(task_profile_for_compat(decision))
    except ValueError:
        return DiagnosisArtifactType.FAULT_DIAGNOSIS


def _first(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _row_values(rows: list[dict[str, Any]], *keys: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = str(row.get(key) or "").strip()
            if value:
                values.append(value)
    return list(dict.fromkeys(values))


def _artifact_fault_codes(
    request: DiagnosisRequest,
    decision: SingleAgentDecision,
    row_fault_codes: list[str],
) -> list[str]:
    codes: list[str] = []
    if request.fault_code_hint:
        codes.append(request.fault_code_hint)
    decision_codes = (decision.objects or {}).get("alarm_codes", [])
    if isinstance(decision_codes, list):
        codes.extend(str(item).strip() for item in decision_codes if str(item or "").strip())
    elif decision_codes:
        codes.append(str(decision_codes).strip())
    codes.extend(row_fault_codes)
    return list(dict.fromkeys(code for code in codes if code))


def _sample_window_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = sorted(
        str(value).strip()
        for row in rows
        if isinstance(row, dict)
        for value in [row.get("create_time") or row.get("timestamp")]
        if str(value or "").strip()
    )
    if not timestamps:
        return {}
    return {
        "start": timestamps[0],
        "end": timestamps[-1],
        "latest_sample_time": timestamps[-1],
        "sample_count": len(rows),
    }


def _freshness_from_sql(sql_artifact: SqlStepArtifact) -> str:
    if sql_artifact.data_state in {"empty", "blocked", "out_of_scope", "skipped"}:
        return "unknown"
    return "current" if sql_artifact.success and (sql_artifact.row_count or 0) > 0 else "unknown"
