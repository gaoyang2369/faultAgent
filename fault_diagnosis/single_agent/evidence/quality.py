"""Evidence-chain quality checks and output guardrails."""

from __future__ import annotations

import re
from typing import Any

from ...diagnosis.contracts import EvidenceBundle, EvidenceItem
from ..contracts import SingleAgentDecision
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
) -> dict[str, Any]:
    """Build a lightweight output guardrail result for trace and artifact metadata."""

    warnings: list[str] = []
    if not final_answer.strip():
        warnings.append("final_answer_empty")
    if decision is not None and decision.primary_task_type == "action_request":
        if re.search(r"已(?:重启|停机|关闭告警|屏蔽告警|修改|改成|派发|下发|执行)", final_answer):
            warnings.append("unsafe_action_execution_claim")
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
    return {
        "passed": not warnings,
        "warnings": warnings,
        "bundle_id": bundle.bundle_id if bundle is not None else None,
        "evidence_count": len(bundle.evidence_items) if bundle is not None else 0,
        "claim_count": len(bundle.claims) if bundle is not None else 0,
    }


def _first_asset_id(items: list[EvidenceItem]) -> str | None:
    return first_non_empty([item.asset_id for item in items])
