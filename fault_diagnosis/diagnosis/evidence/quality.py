"""Evidence-chain quality checks for diagnosis artifacts."""

from __future__ import annotations

from typing import Any

from ..contracts import EvidenceBundle, EvidenceItem


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


def _first_asset_id(items: list[EvidenceItem]) -> str:
    for item in items:
        asset_id = str(item.asset_id or "").strip()
        if asset_id:
            return asset_id
    return ""
