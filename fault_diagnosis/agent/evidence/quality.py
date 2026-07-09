"""Quality checks for Agent Engine V2 evidence ledgers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import EvidenceLedger


class LedgerValidationResult(BaseModel):
    """Serializable V2 evidence ledger validation result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "ledger_validation_result.v1"
    passed: bool = True
    checks: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def validate_ledger(ledger: EvidenceLedger) -> LedgerValidationResult:
    evidence_ids = {_evidence_id(item) for item in ledger.evidence_items if _evidence_id(item)}
    filtered_unauthorized = set(str(item) for item in ledger.quality_checks.get("filtered_unauthorized_evidence_ids", []) or [])
    claim_refs = [
        ref
        for claim in ledger.claims
        for ref in [*_strings(claim.get("supporting_evidence_ids")), *_strings(claim.get("contradicting_evidence_ids"))]
    ]
    dangling_refs = sorted({ref for ref in claim_refs if ref not in evidence_ids})
    unauthorized_refs = sorted({ref for ref in claim_refs if ref in filtered_unauthorized})
    final_claims = [claim for claim in ledger.claims if str(claim.get("status") or "candidate") == "final"]
    final_claims_without_evidence = [
        str(claim.get("claim_id") or "")
        for claim in final_claims
        if not _strings(claim.get("supporting_evidence_ids"))
    ]
    all_claims_without_evidence = [
        str(claim.get("claim_id") or "")
        for claim in ledger.claims
        if not _strings(claim.get("supporting_evidence_ids"))
    ]
    missing_evidence = _missing_evidence(ledger.claims)
    stale_ids = [_evidence_id(item) for item in ledger.evidence_items if _is_stale(item)]
    stale_disclosed = not stale_ids or _has_disclosure(ledger, "stale_evidence_disclosure") or _has_current_refresh(ledger)
    missing_disclosed = not missing_evidence or _has_disclosure(ledger, "missing_evidence_disclosure")
    final_claim_ids = eligible_final_claim_ids(ledger)
    no_final_claims = bool(ledger.evidence_items) and not final_claim_ids
    checks = {
        "evidence_count": len(ledger.evidence_items),
        "claim_count": len(ledger.claims),
        "has_final_claims": bool(final_claim_ids),
        "no_final_claims": no_final_claims,
        "all_final_claims_have_evidence": not final_claims_without_evidence,
        "final_claims_without_evidence": [item for item in final_claims_without_evidence if item],
        "all_claims_have_evidence": bool(ledger.claims) and not all_claims_without_evidence,
        "claims_without_evidence": [item for item in all_claims_without_evidence if item],
        "no_dangling_evidence_refs": not dangling_refs,
        "dangling_evidence_refs": dangling_refs,
        "missing_evidence": missing_evidence,
        "missing_evidence_disclosed": missing_disclosed,
        "stale_evidence_ids": [item for item in stale_ids if item],
        "stale_evidence_disclosed": stale_disclosed,
        "stale_evidence_refreshed": _has_current_refresh(ledger),
        "filtered_unauthorized_evidence_ids": sorted(filtered_unauthorized),
        "no_unauthorized_evidence_refs": not unauthorized_refs,
        "unauthorized_evidence_refs": unauthorized_refs,
    }
    warnings = [
        key
        for key, failed in (
            ("no_final_claims", no_final_claims),
            ("final_claim_without_evidence", bool(final_claims_without_evidence)),
            ("dangling_evidence_refs", bool(dangling_refs)),
            ("missing_evidence_not_disclosed", not missing_disclosed),
            ("stale_evidence_not_disclosed", not stale_disclosed),
            ("unauthorized_evidence_reference", bool(unauthorized_refs)),
        )
        if failed
    ]
    return LedgerValidationResult(passed=not warnings, checks=checks, warnings=warnings)


def eligible_final_claim_ids(ledger: EvidenceLedger) -> list[str]:
    evidence_ids = {_evidence_id(item) for item in ledger.evidence_items if _evidence_id(item)}
    filtered_unauthorized = set(str(item) for item in ledger.quality_checks.get("filtered_unauthorized_evidence_ids", []) or [])
    final_ids: list[str] = []
    for claim in ledger.claims:
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            continue
        status = str(claim.get("status") or "candidate")
        if status not in {"candidate", "confirmed", "final"}:
            continue
        support = _strings(claim.get("supporting_evidence_ids"))
        refs = [*support, *_strings(claim.get("contradicting_evidence_ids"))]
        if not support:
            continue
        if any(ref not in evidence_ids or ref in filtered_unauthorized for ref in refs):
            continue
        final_ids.append(claim_id)
    return final_ids


def _evidence_id(item: dict[str, Any]) -> str:
    return str(item.get("evidence_id") or "").strip()


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _missing_evidence(claims: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for claim in claims:
        for item in _strings(claim.get("missing_evidence")):
            if item not in values:
                values.append(item)
    return values


def _has_disclosure(ledger: EvidenceLedger, evidence_type: str) -> bool:
    return any(str(item.get("evidence_type") or "") == evidence_type for item in ledger.evidence_items)


def _has_current_refresh(ledger: EvidenceLedger) -> bool:
    for item in ledger.evidence_items:
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        freshness = str(quality.get("freshness") or item.get("freshness") or "").lower()
        evidence_type = str(item.get("evidence_type") or "")
        if freshness in {"current", "recent"} and evidence_type in {"device_status", "metric_snapshot", "timeseries_feature"}:
            return True
    return False


def _is_stale(item: dict[str, Any]) -> bool:
    quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    text = " ".join(
        str(value)
        for value in (
            quality.get("freshness"),
            metadata.get("freshness"),
            metadata.get("currentness_level"),
            metadata.get("data_currentness_level"),
            content.get("currentness_level"),
            content.get("currentness_warning"),
            item.get("summary"),
        )
        if value is not None
    ).lower()
    return any(keyword in text for keyword in ("stale", "已滞后", "滞后", "非实时", "不代表实时"))
