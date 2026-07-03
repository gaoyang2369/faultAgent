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
    payload.update(
        {
            "device": request.equipment_hint or _first((decision.objects or {}).get("device_ids")),
            "asset_id": request.equipment_hint or _first((decision.objects or {}).get("device_ids")),
            "source_table": sql_artifact.source_table,
            "sql_artifact_id": trace.trace_id,
            "analysis_artifact_id": trace.trace_id,
            "evidence_bundle_id": evidence_bundle.bundle_id if evidence_bundle is not None else None,
            "fault_codes": [request.fault_code_hint] if request.fault_code_hint else (decision.objects or {}).get("alarm_codes", []),
            "data_window": decision.time_window or {},
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


def _freshness_from_sql(sql_artifact: SqlStepArtifact) -> str:
    if sql_artifact.data_state in {"empty", "blocked", "out_of_scope", "skipped"}:
        return "unknown"
    return "current" if sql_artifact.success and (sql_artifact.row_count or 0) > 0 else "unknown"
