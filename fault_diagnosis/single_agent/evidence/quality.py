"""Evidence-chain quality checks and output guardrails."""

from __future__ import annotations

import re
from typing import Any

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    EvidenceBundle,
    EvidenceItem,
    KnowledgeStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ..contracts import SingleAgentDecision
from ..compat import route_is_action_request
from ..output.contracts import RenderedAnswer
from ..output.renderers import ACTION_SAFE_FALLBACK, DANGEROUS_ACTION_COMPLETION_PATTERNS
from .utils import first_non_empty


def validate_evidence_bundle(bundle: EvidenceBundle) -> dict[str, Any]:
    """Return deterministic evidence-chain quality checks."""

    evidence_ids = {item.evidence_id for item in bundle.evidence_items if item.evidence_id}
    claim_refs = [
        evidence_id
        for claim in bundle.claims
        for evidence_id in [*claim.supporting_evidence_ids, *claim.contradicting_evidence_ids]
    ]
    dangling_refs = sorted({evidence_id for evidence_id in claim_refs if evidence_id not in evidence_ids})
    missing_evidence_items = [
        item
        for claim in bundle.claims
        for item in claim.missing_evidence
        if str(item or "").strip()
    ]
    evidence_types = {item.evidence_type for item in bundle.evidence_items}
    source_types = {item.source_type for item in bundle.evidence_items}
    return {
        "has_asset": bool(bundle.task.get("asset_id") or _first_asset_id(bundle.evidence_items)),
        "has_user_request": any(item.source_type == "user" for item in bundle.evidence_items),
        "has_current_status": any(item.evidence_type in {"device_status", "metric_snapshot"} for item in bundle.evidence_items),
        "has_alarm_history": "alarm_event" in evidence_types,
        "has_manual_reference": "knowledge_base" in source_types,
        "has_timeseries_feature": "timeseries_feature" in evidence_types,
        "all_claims_have_evidence": bool(bundle.claims) and all(claim.supporting_evidence_ids for claim in bundle.claims),
        "no_dangling_evidence_refs": not dangling_refs,
        "dangling_evidence_refs": dangling_refs,
        "missing_evidence_disclosed": bool(missing_evidence_items) or all(not claim.missing_evidence for claim in bundle.claims),
        "evidence_count": len(bundle.evidence_items),
        "claim_count": len(bundle.claims),
    }


def build_output_guardrail_result(
    final_answer: str,
    bundle: EvidenceBundle | None,
    decision: SingleAgentDecision | None = None,
    rendered_answer: RenderedAnswer | None = None,
    sql_artifact: SqlStepArtifact | None = None,
    knowledge_artifact: KnowledgeStepArtifact | None = None,
    analysis_artifact: AnalysisStepArtifact | None = None,
    workorder_suggestion: WorkOrderSuggestion | None = None,
    referenced_artifact: DiagnosisArtifactEnvelope | None = None,
) -> dict[str, Any]:
    """Build a lightweight output guardrail result for trace and artifact metadata."""

    warnings: list[str] = []
    if not final_answer.strip():
        warnings.append("final_answer_empty")
    if rendered_answer is not None:
        warnings.extend(_validate_rendered_answer(rendered_answer))
    if decision is not None and route_is_action_request(decision):
        if _contains_dangerous_action_completion(final_answer):
            warnings.append("unsafe_action_execution_claim")
    if decision is not None and decision.action_type in {"create_workorder", "dispatch_workorder"}:
        if _contains_workorder_completion(final_answer):
            warnings.append("unsafe_workorder_completion_claim")
    invariant_result = validate_artifact_invariants(
        decision=decision or SingleAgentDecision(),
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
        referenced_artifact=referenced_artifact,
        final_answer=final_answer,
    )
    warnings.extend(invariant_result.get("warnings", []))
    quality_checks = bundle.quality_checks if bundle is not None else {}
    if quality_checks and not quality_checks.get("no_dangling_evidence_refs", True):
        warnings.append("dangling_evidence_refs")
    if quality_checks and not quality_checks.get("all_claims_have_evidence", True):
        warnings.append("claim_without_supporting_evidence")
    if quality_checks and not quality_checks.get("no_unauthorized_evidence_refs", True):
        warnings.append("unauthorized_evidence_reference")
    authorization = decision.authorization if decision is not None else {}
    if authorization.get("mode") == "degrade" and "权限" not in final_answer:
        warnings.append("permission_denial_not_disclosed")
    guest_status_only = (decision.access_scope or {}).get("authorized_purpose") == "status_or_visualization_only" if decision else False
    if guest_status_only and re.search(r"(?:根因|诊断结论|健康评分)[：:]", final_answer):
        warnings.append("guest_diagnosis_claim")
    safe_rewrite = ""
    if decision is not None and route_is_action_request(decision) and "unsafe_action_execution_claim" in warnings:
        safe_rewrite = ACTION_SAFE_FALLBACK
    return {
        "passed": not warnings,
        "warnings": warnings,
        "safe_rewrite": safe_rewrite,
        "bundle_id": bundle.bundle_id if bundle is not None else None,
        "evidence_count": len(bundle.evidence_items) if bundle is not None else 0,
        "claim_count": len(bundle.claims) if bundle is not None else 0,
        "template_id": rendered_answer.template_id if rendered_answer is not None else None,
        "used_evidence_ids": rendered_answer.used_evidence_ids if rendered_answer is not None else [],
        "missing_evidence": rendered_answer.missing_evidence if rendered_answer is not None else [],
        "invariants": invariant_result,
    }


def validate_artifact_invariants(
    *,
    decision: SingleAgentDecision,
    sql_artifact: SqlStepArtifact | None,
    knowledge_artifact: KnowledgeStepArtifact | None,
    analysis_artifact: AnalysisStepArtifact | None,
    workorder_suggestion: WorkOrderSuggestion | None,
    referenced_artifact: DiagnosisArtifactEnvelope | None = None,
    final_answer: str = "",
) -> dict[str, Any]:
    """Validate deterministic cross-stage consistency invariants."""

    warnings: list[str] = []
    workorder_reason = str(getattr(workorder_suggestion, "reason", "") or "")
    if _sql_has_rows(sql_artifact) and _claims_sql_returned_no_data(workorder_reason):
        warnings.append("sql_rows_contradict_workorder_no_data_reason")
    freshness = _referenced_freshness_text(referenced_artifact)
    stale = any(keyword in freshness for keyword in ("已滞后", "滞后", "stale", "非实时", "不代表实时"))
    if stale and not any(keyword in f"{workorder_reason} {final_answer}" for keyword in ("滞后", "采样窗口", "非实时", "不代表实时")):
        warnings.append("stale_referenced_data_not_disclosed")
    knowledge_text = str(getattr(knowledge_artifact, "raw_output", "") or "")
    knowledge_codes = set(re.findall(r"(?<![A-Z0-9])([A-Z]\d{3,5})(?![A-Z0-9])", knowledge_text, flags=re.I))
    if knowledge_codes and any(keyword in final_answer for keyword in ("知识库无结果", "知识检索未命中", "没有知识库结果")):
        warnings.append("knowledge_hit_contradicted_by_final_answer")
    if decision.action_type in {"create_workorder", "dispatch_workorder"} and _contains_workorder_completion(final_answer):
        warnings.append("unsafe_workorder_completion_claim")
    if decision.plan_mode == "workorder_decision_from_artifact" and not decision.referenced_artifact_id:
        warnings.append("artifact_followup_missing_referenced_artifact")
    return {"passed": not warnings, "warnings": list(dict.fromkeys(warnings))}


def _first_asset_id(items: list[EvidenceItem]) -> str | None:
    return first_non_empty([item.asset_id for item in items])


def _validate_rendered_answer(rendered_answer: RenderedAnswer) -> list[str]:
    warnings: list[str] = []
    if not rendered_answer.content.strip():
        warnings.append("rendered_answer_empty")
    missing_required = [
        section.key
        for section in rendered_answer.sections
        if not section.content.strip()
    ]
    if missing_required:
        warnings.append(f"empty_rendered_sections:{','.join(missing_required)}")
    evidence_required_keys = {
        "diagnosis_conclusion",
        "current_status",
        "key_evidence",
        "possible_causes",
        "recommendations",
        "alarm_explanation",
        "current_alarm_status",
        "severity_assessment",
        "recommended_actions",
        "report_summary",
    }
    missing_evidence_sections = [
        section.key
        for section in rendered_answer.sections
        if section.key in evidence_required_keys and not section.evidence_ids
    ]
    if missing_evidence_sections:
        warnings.append(f"section_without_evidence:{','.join(missing_evidence_sections)}")
    if rendered_answer.missing_evidence and not _discloses_missing_evidence(rendered_answer.content):
        warnings.append("missing_evidence_not_disclosed")
    return warnings


def _discloses_missing_evidence(text: str) -> bool:
    return any(keyword in text for keyword in ("证据不足", "缺少", "不能确认", "无法确认", "暂不能"))


def _contains_dangerous_action_completion(text: str) -> bool:
    if any(pattern in text for pattern in DANGEROUS_ACTION_COMPLETION_PATTERNS):
        return True
    return bool(re.search(r"已(?:重启|停机|关闭告警|屏蔽告警|修改|改成|派发|下发|执行|复位)", text))


def _contains_workorder_completion(text: str) -> bool:
    return bool(re.search(r"已(?:创建|生成|派发|下发)(?:维修)?工单", text or ""))


def _claims_sql_returned_no_data(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return any(
        phrase in compact
        for phrase in (
            "SQL未返回数据",
            "SQL未返回可解析运行数据",
            "SQL没有返回数据",
            "SQL查询无数据",
            "数据库未返回数据",
        )
    )


def _sql_has_rows(sql_artifact: SqlStepArtifact | None) -> bool:
    if sql_artifact is None:
        return False
    if getattr(sql_artifact, "row_count", None):
        return int(sql_artifact.row_count or 0) > 0
    text = f"{sql_artifact.result_preview or ''} {sql_artifact.raw_output or ''}"
    return bool(re.search(r"[\[\(]\s*(?:\{|\'|\"|\d)", text) or re.search(r"\bdevice_name\b|\bcreate_time\b|A\d{3,5}", text))


def _referenced_freshness_text(envelope: DiagnosisArtifactEnvelope | None) -> str:
    if envelope is None:
        return ""
    values: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {
                    "freshness_label",
                    "data_freshness_label",
                    "currentness",
                    "data_currentness_level",
                    "data_currentness_label",
                }:
                    values.append(str(value))
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)

    visit(envelope.payload or {})
    return " ".join(values)
