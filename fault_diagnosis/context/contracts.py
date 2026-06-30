"""Context contracts for deterministic multi-turn diagnosis state."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field


CASE_STATE_SNAPSHOT_VERSION = "case_state_snapshot.v1"


class ContextReference(BaseModel):
    """A user utterance reference to previous context or an explicit object."""

    reference_type: str = "unknown"
    label: str = ""
    value: str | None = None
    source: str = "message"
    confidence: float = 0.0


class PendingAction(BaseModel):
    """A pending action inferred from prior artifacts, not an executed action."""

    action_type: str
    status: str = "pending"
    artifact_id: str | None = None
    reason: str = ""
    required_evidence: list[str] = Field(default_factory=list)


class CaseState(BaseModel):
    """Thread-local diagnosis case projected from diagnosis artifacts."""

    case_id: str
    thread_id: str
    active_asset: str | None = None
    active_fault_codes: list[str] = Field(default_factory=list)
    active_time_window: dict[str, Any] = Field(default_factory=dict)
    latest_artifact_id: str | None = None
    latest_report_id: str | None = None
    latest_evidence_bundle_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("latest_evidence_bundle_id", "last_evidence_bundle_id"),
    )
    last_report_url: str | None = None
    status_level: str | None = None
    severity: str | None = None
    priority: str | None = None
    freshness_label: str | None = None
    currentness: str | None = None
    latest_sample_time: str | None = None
    sample_count: int | None = None
    current_event: str | None = None
    key_phenomenon: str | None = None
    diagnosis_summary: str | None = None
    initial_assessment: str | None = None
    next_action: str | None = None
    evidence_summary: list[str] = Field(default_factory=list)
    pending_actions: list[PendingAction] = Field(default_factory=list)
    available_followups: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    evidence_freshness: str = "unknown"
    projection_warnings: list[str] = Field(default_factory=list)
    source: str = "artifact_projection"

    @property
    def last_evidence_bundle_id(self) -> str | None:
        """Legacy alias used by the existing single-agent workflow."""

        return self.latest_evidence_bundle_id


class ConversationDiagnosisState(BaseModel):
    """Per-thread case collection used for deterministic context filling."""

    thread_id: str
    active_case_id: str | None = None
    cases: list[CaseState] = Field(default_factory=list)

    @property
    def active_case(self) -> CaseState | None:
        if self.active_case_id:
            for case in self.cases:
                if case.case_id == self.active_case_id:
                    return case
        return self.cases[0] if self.cases else None


class ResolvedContext(BaseModel):
    """Unified context decision consumed by routing and output contracts."""

    relation_to_previous: str = "new_case"
    active_case_id: str | None = None
    referenced_artifact_id: str | None = None
    referenced_case_id: str | None = None
    referenced_report_id: str | None = None
    inherited_slots: dict[str, Any] = Field(default_factory=dict)
    pending_actions: list[PendingAction] = Field(default_factory=list)
    stale_evidence: bool = False
    missing_context: list[str] = Field(default_factory=list)
    context_resolution_reason: str = ""
    references: list[str] = Field(default_factory=list)
    candidates: dict[str, list[str]] = Field(default_factory=dict)
    resolved: bool = False
    source: str = "none"
    used_active_asset: bool = False
    used_active_fault_codes: bool = False
    active_asset: str | None = None
    active_fault_codes: list[str] = Field(default_factory=list)
    active_time_window: dict[str, Any] = Field(default_factory=dict)
    last_evidence_bundle_id: str | None = None
    last_report_url: str | None = None
    evidence_mode: str = "collect_new"
    should_refresh_runtime_data: bool = False

    def legacy_context_resolution(self) -> dict[str, Any]:
        """Return the dict shape expected by existing routes, tests and payloads."""

        return {
            "resolved": self.resolved,
            "source": self.source,
            "references": list(self.references),
            "used_active_asset": self.used_active_asset,
            "used_active_fault_codes": self.used_active_fault_codes,
            "active_asset": self.active_asset,
            "active_fault_codes": list(self.active_fault_codes),
            "active_time_window": self.active_time_window,
            "last_evidence_bundle_id": self.last_evidence_bundle_id,
            "last_report_url": self.last_report_url,
            "unresolved_questions": list(self.missing_context),
            "candidates": self.candidates,
            "relation_to_previous": self.relation_to_previous,
            "referenced_artifact_id": self.referenced_artifact_id,
            "referenced_report_id": self.referenced_report_id,
            "inherited_slots": self.inherited_slots,
            "pending_actions": [item.model_dump(exclude_none=True) for item in self.pending_actions],
            "stale_evidence": self.stale_evidence,
            "missing_context": list(self.missing_context),
            "context_resolution_reason": self.context_resolution_reason,
        }


def summarize_resolved_context(value: Any) -> dict[str, Any]:
    """Return the compact debug contract used by plan, complete and trace metadata."""

    data = _coerce_context_dict(value)
    pending_actions = _coerce_pending_actions(data.get("pending_actions"))
    candidates = data.get("candidates") if isinstance(data.get("candidates"), dict) else {}
    candidate_counts = {
        str(key): len(value)
        for key, value in candidates.items()
        if isinstance(value, list)
    }
    candidate_counts["total"] = sum(candidate_counts.values())
    summary = {
        "relation_to_previous": data.get("relation_to_previous") or "new_case",
        "active_case_id": data.get("active_case_id"),
        "referenced_artifact_id": data.get("referenced_artifact_id"),
        "referenced_report_id": data.get("referenced_report_id"),
        "inherited_slots": data.get("inherited_slots") if isinstance(data.get("inherited_slots"), dict) else {},
        "pending_actions": [
            {
                "action_type": item.get("action_type"),
                "status": item.get("status"),
                "artifact_id": item.get("artifact_id"),
                "required_evidence_count": len(item.get("required_evidence") or []),
            }
            for item in pending_actions
        ],
        "pending_action_count": len(pending_actions),
        "pending_action_types": list(dict.fromkeys(str(item.get("action_type")) for item in pending_actions if item.get("action_type"))),
        "stale_evidence": bool(data.get("stale_evidence")),
        "missing_context": list(data.get("missing_context") or []),
        "context_resolution_reason": str(data.get("context_resolution_reason") or ""),
        "candidates_count": candidate_counts,
        "source": data.get("source"),
        "used_active_asset": data.get("used_active_asset"),
        "used_active_fault_codes": data.get("used_active_fault_codes"),
    }
    if data.get("should_refresh_runtime_data") is not None:
        summary["should_refresh_runtime_data"] = bool(data.get("should_refresh_runtime_data"))
    if data.get("evidence_mode"):
        summary["evidence_mode"] = data.get("evidence_mode")
    return summary


def _coerce_context_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _coerce_pending_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, Any]] = []
    for item in value:
        if hasattr(item, "model_dump"):
            actions.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            actions.append(dict(item))
    return actions
