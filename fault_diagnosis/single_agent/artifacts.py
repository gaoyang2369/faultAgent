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
    WorkOrderSuggestion,
)
from .contracts import AgentTrace, SingleAgentDecision


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
    workflow_artifacts: dict[str, object] | None = None,
    auth: dict[str, Any] | None = None,
    authorization: dict[str, Any] | None = None,
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
        "request": request.model_dump(exclude_none=True),
        "decision": decision.model_dump(),
        "sql_artifact": sql_artifact.model_dump(exclude_none=True),
        "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
        "analysis_artifact": analysis_artifact.model_dump(exclude_none=True),
        "workorder_decision": workorder_suggestion.model_dump(exclude_none=True),
        "report_artifact": report_artifact.model_dump(exclude_none=True),
        "trace": trace.model_dump(exclude_none=True),
        "output_guardrail": output_guardrail or {},
        "workflow_artifacts": workflow_artifacts or {},
        "auth": auth or {},
        "authorization": authorization or {},
    }
    if evidence_bundle is not None:
        payload["evidence_bundle"] = evidence_bundle.model_dump(exclude_none=True)
    return DiagnosisArtifactEnvelope(
        workflow_type=_artifact_type_from_decision(decision),
        thread_id=thread_id,
        created_at=datetime.now().isoformat(),
        request_summary=request.analysis_goal or request.user_message,
        final_answer=final_answer,
        report_filename=report_artifact.report_filename,
        payload=payload,
        evidence=evidence,
    )


def _artifact_type_from_decision(decision: SingleAgentDecision) -> DiagnosisArtifactType:
    try:
        return DiagnosisArtifactType(decision.primary_task_type)
    except ValueError:
        return DiagnosisArtifactType.FAULT_DIAGNOSIS
