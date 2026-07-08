"""Projection from V2 EvidenceLedger to legacy EvidenceBundle."""

from __future__ import annotations

from typing import Any

from ..contracts import EvidenceLedger
from fault_diagnosis.domain.diagnosis.contracts import Claim, EvidenceBundle, EvidenceItem
from fault_diagnosis.domain.diagnosis.evidence.quality import validate_evidence_bundle
from .quality import eligible_final_claim_ids


def project_ledger_to_evidence_bundle(
    ledger: EvidenceLedger,
    *,
    trace_id: str = "",
    task: dict[str, Any] | None = None,
) -> EvidenceBundle:
    evidence_items = [_evidence_item(item) for item in ledger.evidence_items]
    evidence_ids = {item.evidence_id for item in evidence_items if item.evidence_id}
    filtered = set(str(item) for item in ledger.quality_checks.get("filtered_unauthorized_evidence_ids", []) or [])
    claims = [
        claim
        for claim in (_claim(item) for item in ledger.claims)
        if _claim_refs_authorized_and_present(claim, evidence_ids=evidence_ids, filtered=filtered)
    ]
    projected = EvidenceBundle(
        bundle_id=ledger.ledger_id or f"bundle_{trace_id or 'unknown'}",
        trace_id=trace_id or str(ledger.task.get("trace_id") or ""),
        task={**ledger.task, **dict(task or {})},
        evidence_items=evidence_items,
        claims=claims,
        final_claim_ids=[claim_id for claim_id in eligible_final_claim_ids(ledger) if any(claim.claim_id == claim_id for claim in claims)],
        artifacts={"v2_ledger_id": ledger.ledger_id, "artifact_refs": list(ledger.artifact_refs)},
    )
    projected.quality_checks = {**validate_evidence_bundle(projected), **ledger.quality_checks}
    return projected


def _evidence_item(item: dict[str, Any]) -> EvidenceItem:
    return EvidenceItem.model_validate(item)


def _claim(item: dict[str, Any]) -> Claim:
    return Claim.model_validate(item)


def _claim_refs_authorized_and_present(claim: Claim, *, evidence_ids: set[str], filtered: set[str]) -> bool:
    refs = [*claim.supporting_evidence_ids, *claim.contradicting_evidence_ids]
    return all(ref in evidence_ids and ref not in filtered for ref in refs)
