"""Claim helpers for Agent Engine V2 evidence ledgers."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import Claim, ClaimConfidence


def build_v2_claim(
    *,
    claim_id: str,
    claim_type: str,
    statement: str,
    supporting_evidence_ids: list[str],
    status: str = "candidate",
    asset_id: str | None = None,
    missing_evidence: list[str] | None = None,
    confidence: str = "medium",
    created_by: str = "agent_engine_v2",
) -> dict[str, Any]:
    level = confidence if confidence in {"high", "medium", "low"} else "medium"
    claim = Claim(
        claim_id=claim_id,
        claim_type=claim_type,
        asset_id=asset_id,
        statement=statement,
        confidence=ClaimConfidence(level=level),
        supporting_evidence_ids=list(supporting_evidence_ids),
        missing_evidence=list(missing_evidence or []),
        status=status if status in {"candidate", "confirmed", "rejected", "final"} else "candidate",
        created_by=created_by,
    )
    return claim.model_dump(mode="json", exclude_none=True)


def build_claims_from_outputs(*, output: dict[str, Any], evidence_ids: list[str], node_id: str) -> list[dict[str, Any]]:
    summary = str(output.get("summary") or output.get("conclusion") or "").strip()
    if not summary:
        return []
    return [
        build_v2_claim(
            claim_id=f"claim_{node_id}",
            claim_type=str(output.get("claim_type") or "node_summary"),
            statement=summary,
            supporting_evidence_ids=evidence_ids,
            status=str(output.get("claim_status") or "candidate"),
            missing_evidence=list(output.get("missing_evidence") or []),
            created_by=node_id,
        )
    ]
