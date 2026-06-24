"""Evidence bundle facade for the restricted single-agent runtime."""

from __future__ import annotations

import re
from typing import Any

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ...diagnosis.analysis.contracts import StructuredAnalysisArtifact
from ..contracts import SingleAgentDecision
from .claims import build_claims
from .knowledge import build_knowledge_evidence_items
from .quality import build_output_guardrail_result, validate_evidence_bundle
from .sql import build_sql_evidence_items
from ..support.serialization import preview, sanitize_for_json, stringify

WORKFLOW_ID = "WF_FAULT_DIAGNOSIS_V1"
WORKFLOW_VERSION = "1.0.0"


def initialize_evidence_bundle(
    *,
    trace_id: str,
    request: DiagnosisRequest,
    decision: SingleAgentDecision,
) -> EvidenceBundle:
    """Create the request-scoped empty evidence ledger."""

    workflow_policy = decision.workflow_policy or {}
    workflow_id = str(workflow_policy.get("workflow_id") or WORKFLOW_ID)
    workflow_version = str(workflow_policy.get("version") or WORKFLOW_VERSION)
    task = {
        "task_type": decision.primary_task_type or "fault_diagnosis",
        "primary_task_type": decision.primary_task_type or "fault_diagnosis",
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "policy_id": workflow_policy.get("policy_id"),
        "route_confidence": decision.route_confidence,
        "user_query": request.user_message,
        "user_identity": request.user_identity,
        "asset_id": request.equipment_hint,
        "objects": decision.objects,
        "time_window": decision.time_window,
        "subgoals": decision.subgoals,
        "missing_slots": decision.missing_slots,
        "risk_level": decision.risk_level,
        "requested_output": decision.requested_output,
        "symptom": request.metric_hint or request.fault_code_hint or request.analysis_goal,
        "time_range_hint": request.time_range_hint,
        "requires_sql": decision.needs_sql,
        "requires_knowledge": decision.needs_knowledge,
        "requires_report": decision.needs_report,
        "enabled_nodes": decision.enabled_nodes,
        "guardrails": decision.guardrails,
    }
    return EvidenceBundle(
        bundle_id=_bundle_id(trace_id),
        trace_id=trace_id,
        task={key: value for key, value in task.items() if value not in (None, "", [])},
        artifacts={"workflow_id": workflow_id, "workflow_version": workflow_version},
    )


def build_evidence_bundle(
    *,
    trace_id: str,
    request: DiagnosisRequest,
    decision: SingleAgentDecision,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion,
    report_artifact: ReportStepArtifact,
    structured_analysis_artifact: StructuredAnalysisArtifact | None = None,
) -> EvidenceBundle:
    """Build and validate a complete evidence bundle for one run."""

    bundle = initialize_evidence_bundle(trace_id=trace_id, request=request, decision=decision)
    evidence_items = _dedupe_evidence(
        [
            _user_request_evidence(request),
            *build_sql_evidence_items(sql_artifact, request=request),
            *build_knowledge_evidence_items(knowledge_artifact, request=request),
            *(structured_analysis_artifact.evidence_items if structured_analysis_artifact is not None else []),
        ]
    )
    evidence_ids = [item.evidence_id for item in evidence_items if item.evidence_id]
    claims = _dedupe_claims(
        [
            *(structured_analysis_artifact.claims if structured_analysis_artifact is not None else []),
            *build_claims(
                request=request,
                analysis_artifact=analysis_artifact,
                workorder_suggestion=workorder_suggestion,
                evidence_ids=evidence_ids,
            ),
        ]
    )
    final_claim_ids = [claim.claim_id for claim in claims if claim.status in {"candidate", "confirmed", "final"}]
    bundle.evidence_items = evidence_items
    bundle.claims = claims
    bundle.final_claim_ids = final_claim_ids
    bundle.artifacts.update(
        {
            "sql_success": sql_artifact.success,
            "knowledge_success": knowledge_artifact.success,
            "report_filename": report_artifact.report_filename,
            "report_success": report_artifact.success,
        }
    )
    bundle.quality_checks = validate_evidence_bundle(bundle)
    return bundle


def build_tool_evidence_preview(*, tool_name: str, output: Any) -> list[dict[str, Any]]:
    """Build compact evidence summaries for tool_end SSE events."""

    if tool_name == "sql_db_query":
        artifact = SqlStepArtifact(
            success=True,
            summary="SQL 工具返回运行数据",
            raw_output=stringify(output),
            result_preview=preview(output),
        )
        return [_tool_evidence_payload(item) for item in build_sql_evidence_items(artifact, request=None)]
    if tool_name == "query_knowledge_base":
        raw_output = stringify(output)
        artifact = KnowledgeStepArtifact(
            success=bool(raw_output.strip()),
            query="",
            snippets=[item.strip() for item in raw_output.split("\n\n") if item.strip()][:3],
            raw_output=raw_output,
            error=None if raw_output.strip() else "知识库未返回内容",
        )
        return [_tool_evidence_payload(item) for item in build_knowledge_evidence_items(artifact, request=None)]
    return []


def _bundle_id(trace_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_]+", "_", trace_id or "unknown").strip("_")
    return f"bundle_{suffix or 'unknown'}"


def _user_request_evidence(request: DiagnosisRequest) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="ev_user_request",
        evidence_type="user_statement",
        source_type="user",
        source_name="chat_message",
        asset_id=request.equipment_hint,
        content={
            "user_message": request.user_message,
            "analysis_goal": request.analysis_goal,
            "equipment_hint": request.equipment_hint,
            "metric_hint": request.metric_hint,
            "fault_code_hint": request.fault_code_hint,
            "time_range_hint": request.time_range_hint,
        },
        summary=f"用户请求：{request.analysis_goal or request.user_message}",
        quality=EvidenceQuality(reliability="medium", freshness="current", relevance="high", completeness="partial"),
        metadata={"user_identity": request.user_identity},
        title="用户请求",
        importance="medium",
    )


def _tool_evidence_payload(item: EvidenceItem) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "evidence_type": item.evidence_type,
        "source_type": item.source_type,
        "summary": item.summary,
        "quality": item.quality.model_dump(),
    }


def _dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    deduped: list[EvidenceItem] = []
    seen: set[str] = set()
    for item in items:
        key = item.evidence_id or item.summary
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_claims(items: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = getattr(item, "claim_id", "") or getattr(item, "statement", "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def dump_evidence_bundle(bundle: EvidenceBundle | None) -> dict[str, Any] | None:
    """Serialize bundle with sanitization for trace metadata."""

    if bundle is None:
        return None
    return sanitize_for_json(bundle.model_dump(exclude_none=True))
