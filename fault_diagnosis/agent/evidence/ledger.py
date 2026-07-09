"""First-class V2 evidence ledger writer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import EvidenceLedger
from fault_diagnosis.domain.security.contracts import AuthContext
from .quality import LedgerValidationResult, eligible_final_claim_ids, validate_ledger


class LedgerCommitResult(BaseModel):
    """Result of committing evidence or claims to a V2 ledger."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "ledger_commit_result.v1"
    refs: list[str] = Field(default_factory=list)
    added: int = 0
    deduped: int = 0
    filtered_unauthorized: list[str] = Field(default_factory=list)


class EvidenceLedgerWriter:
    """Normalize, authorize, dedupe and finalize a V2 EvidenceLedger."""

    def __init__(self, ledger: EvidenceLedger, *, auth_context: AuthContext | None = None) -> None:
        self.ledger = ledger
        self.auth_context = auth_context

    def commit_evidence(
        self,
        evidence_items: list[Any],
        *,
        node: dict[str, Any] | None = None,
        tool_call_refs: list[str] | None = None,
    ) -> LedgerCommitResult:
        refs: list[str] = []
        added = 0
        deduped = 0
        filtered: list[str] = []
        for index, raw in enumerate(evidence_items, start=1):
            item = _normalize_evidence(raw, node=node, index=index, tool_call_refs=tool_call_refs, auth_context=self.auth_context)
            evidence_id = str(item.get("evidence_id") or f"ev_{len(self.ledger.evidence_items) + index}")
            item["evidence_id"] = evidence_id
            if not _is_authorized(item):
                filtered.append(evidence_id)
                continue
            existing = _find_existing_evidence(self.ledger, item)
            if existing is not None:
                _merge_evidence(existing, item)
                refs.append(str(existing.get("evidence_id") or evidence_id))
                deduped += 1
                continue
            self.ledger.evidence_items.append(item)
            refs.append(evidence_id)
            added += 1
        if filtered:
            current = list(self.ledger.quality_checks.get("filtered_unauthorized_evidence_ids", []) or [])
            self.ledger.quality_checks["filtered_unauthorized_evidence_ids"] = _dedupe([*current, *filtered])
        self._update_counts()
        return LedgerCommitResult(refs=refs, added=added, deduped=deduped, filtered_unauthorized=filtered)

    def commit_claims(self, claims: list[Any]) -> LedgerCommitResult:
        refs: list[str] = []
        added = 0
        deduped = 0
        for index, raw in enumerate(claims, start=1):
            claim = _normalize_claim(raw, index=index)
            claim_id = str(claim.get("claim_id") or f"claim_{len(self.ledger.claims) + index}")
            claim["claim_id"] = claim_id
            existing = next((item for item in self.ledger.claims if item.get("claim_id") == claim_id), None)
            if existing is not None:
                existing.update({key: value for key, value in claim.items() if value not in (None, "", [])})
                refs.append(claim_id)
                deduped += 1
                continue
            self.ledger.claims.append(claim)
            refs.append(claim_id)
            added += 1
        self.ledger.final_claim_ids = eligible_final_claim_ids(self.ledger)
        self._update_counts()
        return LedgerCommitResult(refs=refs, added=added, deduped=deduped)

    def finalize(self, *, artifact_refs: list[dict[str, Any]] | None = None) -> LedgerValidationResult:
        self._add_missing_evidence_disclosure()
        self._add_stale_evidence_disclosure()
        if artifact_refs:
            self.ledger.artifact_refs = _dedupe_dicts([*self.ledger.artifact_refs, *artifact_refs])
        self.ledger.final_claim_ids = eligible_final_claim_ids(self.ledger)
        validation = validate_ledger(self.ledger)
        self.ledger.quality_checks = {**self.ledger.quality_checks, **validation.checks, "passed": validation.passed, "warnings": validation.warnings}
        self._update_counts()
        return validation

    def _add_missing_evidence_disclosure(self) -> None:
        missing = []
        for claim in self.ledger.claims:
            for item in _strings(claim.get("missing_evidence")):
                if item not in missing:
                    missing.append(item)
        if not missing or any(item.get("evidence_type") == "missing_evidence_disclosure" for item in self.ledger.evidence_items):
            return
        self.commit_evidence(
            [
                {
                    "evidence_id": "ev_manual_missing_evidence_disclosure",
                    "evidence_type": "missing_evidence_disclosure",
                    "source_type": "manual_disclosure",
                    "source_name": "v2_evidence_ledger",
                    "summary": "缺失证据已披露：" + "；".join(missing),
                    "content": {"missing_evidence": missing},
                    "quality": {"reliability": "high", "freshness": "current", "relevance": "high", "completeness": "complete"},
                    "metadata": {"authorized": True, "disclosure": True},
                }
            ]
        )

    def _add_stale_evidence_disclosure(self) -> None:
        stale_ids = [str(item.get("evidence_id") or "") for item in self.ledger.evidence_items if _is_stale(item)]
        if not stale_ids or _has_current_refresh(self.ledger):
            return
        if any(item.get("evidence_type") == "stale_evidence_disclosure" for item in self.ledger.evidence_items):
            return
        self.commit_evidence(
            [
                {
                    "evidence_id": "ev_manual_stale_evidence_disclosure",
                    "evidence_type": "stale_evidence_disclosure",
                    "source_type": "manual_disclosure",
                    "source_name": "v2_evidence_ledger",
                    "summary": "存在滞后证据，输出必须披露其不代表当前实时状态。",
                    "content": {"stale_evidence_ids": [item for item in stale_ids if item]},
                    "quality": {"reliability": "high", "freshness": "current", "relevance": "high", "completeness": "complete"},
                    "metadata": {"authorized": True, "disclosure": True},
                }
            ]
        )

    def _update_counts(self) -> None:
        self.ledger.quality_checks = {
            **self.ledger.quality_checks,
            "evidence_count": len(self.ledger.evidence_items),
            "claim_count": len(self.ledger.claims),
        }


def create_ledger(
    *,
    trace_id: str = "",
    task: dict[str, Any] | None = None,
    auth_context: AuthContext | None = None,
) -> EvidenceLedger:
    ledger = EvidenceLedger(
        ledger_id=f"ledger_{_safe_id(trace_id or 'unknown')}",
        task=dict(task or {}),
    )
    if auth_context is not None:
        ledger.authorization_refs.append({"type": "auth_context", "authorized": True, **auth_context.audit_summary()})
    return ledger


def commit_evidence(
    ledger: EvidenceLedger,
    evidence_items: list[Any],
    *,
    node: dict[str, Any] | None = None,
    auth_context: AuthContext | None = None,
    tool_call_refs: list[str] | None = None,
) -> LedgerCommitResult:
    return EvidenceLedgerWriter(ledger, auth_context=auth_context).commit_evidence(
        evidence_items,
        node=node,
        tool_call_refs=tool_call_refs,
    )


def commit_claims(ledger: EvidenceLedger, claims: list[Any]) -> LedgerCommitResult:
    return EvidenceLedgerWriter(ledger).commit_claims(claims)


def finalize_ledger(
    ledger: EvidenceLedger,
    *,
    auth_context: AuthContext | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
) -> LedgerValidationResult:
    return EvidenceLedgerWriter(ledger, auth_context=auth_context).finalize(artifact_refs=artifact_refs)


def _normalize_evidence(
    raw: Any,
    *,
    node: dict[str, Any] | None,
    index: int,
    tool_call_refs: list[str] | None,
    auth_context: AuthContext | None,
) -> dict[str, Any]:
    item = _model_dump(raw)
    if not item:
        item = {"content": raw}
    node_id = str((node or {}).get("node_id") or "")
    node_type = str((node or {}).get("node_type") or (node or {}).get("type") or "")
    item.setdefault("evidence_id", f"ev_{node_id}_{index}" if node_id else "")
    item.setdefault("evidence_type", "generic")
    item.setdefault("source_type", node_type or "generic")
    item.setdefault("source_name", item.get("source_type") or "generic")
    item.setdefault("summary", str(item.get("title") or item.get("content") or item.get("evidence_type") or "证据项")[:500])
    if node_id:
        item.setdefault("node_id", node_id)
    if node_type:
        item.setdefault("node_type", node_type)
    metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    if tool_call_refs:
        metadata["tool_call_refs"] = list(tool_call_refs)
    explicit_authorized = _explicit_authorized(item)
    if auth_context is not None and explicit_authorized is not False:
        metadata.setdefault("authorized", True)
        metadata.setdefault("access_scope", auth_context.audit_summary())
    item["metadata"] = metadata
    return item


def _normalize_claim(raw: Any, *, index: int) -> dict[str, Any]:
    claim = _model_dump(raw)
    claim.setdefault("claim_id", f"claim_{index}")
    claim.setdefault("claim_type", "generic")
    claim.setdefault("statement", str(claim.get("summary") or claim.get("content") or ""))
    claim.setdefault("supporting_evidence_ids", [])
    claim.setdefault("contradicting_evidence_ids", [])
    claim.setdefault("missing_evidence", [])
    claim.setdefault("status", "candidate")
    claim.setdefault("created_by", "agent_engine_v2")
    return claim


def _is_authorized(item: dict[str, Any]) -> bool:
    explicit = _explicit_authorized(item)
    return explicit is True


def _explicit_authorized(item: dict[str, Any]) -> bool | None:
    if item.get("authorized") is False:
        return False
    if item.get("authorized") is True:
        return True
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    authorization = item.get("authorization") if isinstance(item.get("authorization"), dict) else {}
    if metadata.get("authorized") is False or authorization.get("authorized") is False:
        return False
    if metadata.get("authorized") is True or authorization.get("authorized") is True:
        return True
    return None


def _find_existing_evidence(ledger: EvidenceLedger, item: dict[str, Any]) -> dict[str, Any] | None:
    evidence_id = str(item.get("evidence_id") or "")
    for existing in ledger.evidence_items:
        if evidence_id and existing.get("evidence_id") == evidence_id:
            return existing
    key = _dedupe_key(item)
    return next((existing for existing in ledger.evidence_items if _dedupe_key(existing) == key), None)


def _merge_evidence(existing: dict[str, Any], item: dict[str, Any]) -> None:
    existing_metadata = dict(existing.get("metadata") or {}) if isinstance(existing.get("metadata"), dict) else {}
    item_metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    existing.update({key: value for key, value in item.items() if key != "metadata" and value not in (None, "", [])})
    existing["metadata"] = {**existing_metadata, **item_metadata}


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("source_type") or ""),
        str(item.get("evidence_type") or ""),
        str(item.get("summary") or ""),
    )


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return dict(value) if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if str(item or "").strip()))


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        artifact_type = str(item.get("artifact_type") or item.get("type") or "")
        artifact_id = str(item.get("artifact_id") or item.get("id") or "")
        key = (artifact_type, artifact_id or artifact_type or str(item))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value).strip("_") or "unknown"


def _is_stale(item: dict[str, Any]) -> bool:
    quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    text = " ".join(
        str(value)
        for value in (
            quality.get("freshness"),
            metadata.get("currentness_level"),
            content.get("currentness_level"),
            content.get("currentness_warning"),
            item.get("summary"),
        )
        if value is not None
    ).lower()
    return any(keyword in text for keyword in ("stale", "已滞后", "滞后", "非实时", "不代表实时"))


def _has_current_refresh(ledger: EvidenceLedger) -> bool:
    for item in ledger.evidence_items:
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        freshness = str(quality.get("freshness") or "").lower()
        if freshness in {"current", "recent"} and item.get("evidence_type") in {"device_status", "metric_snapshot", "timeseries_feature"}:
            return True
    return False
