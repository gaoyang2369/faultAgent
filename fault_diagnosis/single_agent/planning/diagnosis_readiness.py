"""Goal-native diagnosis readiness."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..workflow.axes import goal_types, requests_runtime_status

DIAGNOSIS_READINESS_SCHEMA_VERSION = "diagnosis_readiness.v2"

DiagnosisMode = Literal["alarm_triage", "fault_diagnosis", "root_cause_analysis", "health_assessment", "unknown"]


class DiagnosisReadiness(BaseModel):
    schema_version: str = DIAGNOSIS_READINESS_SCHEMA_VERSION
    ready_for_diagnosis: bool = False
    evidence_complete: bool = False
    has_runtime_status: bool = False
    has_manual_reference: bool = False
    has_alarm_or_fault_context: bool = False
    claims_have_supporting_evidence: bool = False
    stale_evidence_disclosed: bool = False
    missing_critical_evidence: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    diagnosis_mode: DiagnosisMode = "unknown"


def build_diagnosis_readiness(*, decision: Any) -> DiagnosisReadiness:
    """Build readiness from GoalSet/context/evidence/runtime axes."""

    goals = set(goal_types(decision))
    context = _to_dict(getattr(decision, "resolved_context", {}) or {})
    nodes = _bool_dict(getattr(decision, "enabled_nodes", {}) or {})
    runtime_tools = set(_strings(getattr(decision, "runtime_tools", []) or []))
    objects = _to_dict(getattr(decision, "objects", {}) or {})
    required = set(_strings(getattr(decision, "required_evidence", []) or []))
    missing_or_stale = set(_strings(getattr(decision, "missing_or_stale_evidence", []) or []))
    mode = _diagnosis_mode(goals, decision)
    stale = bool(context.get("stale_evidence"))
    stale_disclosed = not stale or bool(
        getattr(decision, "should_refresh_runtime_data", False)
        or missing_or_stale.intersection({"evidence_stale", "fresh_runtime_status_required", "latest_realtime_status"})
    )
    has_runtime = bool(
        nodes.get("sql")
        or runtime_tools.intersection({"sql_db_query_checker", "sql_db_query"})
        or context.get("referenced_artifact_id") and not stale
    )
    has_manual = bool(nodes.get("knowledge") or "query_knowledge_base" in runtime_tools)
    has_alarm_or_fault = bool(
        objects.get("alarm_codes")
        or context.get("active_fault_codes")
        or goals.intersection({"explain_fault_code", "diagnose_fault", "assess_severity", "recommend_resolution"})
    )
    blockers: list[str] = []
    missing: list[str] = []
    if mode == "unknown":
        blockers.append("not_diagnosis_task")
    if _needs_device(mode) and not _has_device_context(objects, context):
        blockers.append("missing_device")
        missing.append("device")
    if _needs_runtime(mode, decision) and not has_runtime:
        blockers.append("missing_runtime_status")
        missing.append("runtime_status")
    if _needs_manual_reference(mode, has_alarm_or_fault) and not has_manual:
        blockers.append("missing_manual_reference")
        missing.append("manual_reference")
    if _needs_alarm_or_fault(mode) and not has_alarm_or_fault:
        blockers.append("missing_alarm_or_fault_context")
        missing.append("alarm_or_fault_context")
    if not nodes.get("analysis"):
        blockers.append("missing_analysis_basis")
        missing.append("analysis_basis")
    if context.get("relation_to_previous") == "ambiguous":
        blockers.append("ambiguous_context")
    if not stale_disclosed:
        blockers.append("stale_evidence_without_disclosure")
        missing.append("freshness_disclosure")
    authorization = _to_dict(getattr(decision, "authorization", {}) or {})
    if authorization.get("mode") in {"deny", "clarify"}:
        blockers.append("authorization_not_allow")
    missing.extend(sorted(required.intersection(missing_or_stale)))
    missing = _dedupe(missing)
    blockers = _dedupe(blockers)
    evidence_complete = bool(not blockers and has_runtime and nodes.get("analysis"))
    return DiagnosisReadiness(
        ready_for_diagnosis=evidence_complete,
        evidence_complete=evidence_complete,
        has_runtime_status=has_runtime,
        has_manual_reference=has_manual,
        has_alarm_or_fault_context=has_alarm_or_fault,
        claims_have_supporting_evidence=not bool(missing_or_stale.intersection({"claim_supporting_evidence"})),
        stale_evidence_disclosed=stale_disclosed,
        missing_critical_evidence=missing,
        blocked_reasons=blockers,
        diagnosis_mode=mode,
    )


def summarize_diagnosis_readiness(value: Any) -> dict[str, Any]:
    data = value.model_dump(exclude_none=True) if isinstance(value, DiagnosisReadiness) else _to_dict(value)
    if not data:
        return {}
    missing = list(data.get("missing_critical_evidence") or [])
    blocked = list(data.get("blocked_reasons") or [])
    return {
        "diagnosis_mode": data.get("diagnosis_mode", "unknown"),
        "ready_for_diagnosis": bool(data.get("ready_for_diagnosis", False)),
        "evidence_complete": bool(data.get("evidence_complete", False)),
        "has_runtime_status": bool(data.get("has_runtime_status", False)),
        "has_manual_reference": bool(data.get("has_manual_reference", False)),
        "has_alarm_or_fault_context": bool(data.get("has_alarm_or_fault_context", False)),
        "stale_evidence_disclosed": bool(data.get("stale_evidence_disclosed", False)),
        "missing_critical_evidence_count": len(missing),
        "blocked_reason_count": len(blocked),
    }


def _diagnosis_mode(goals: set[str], decision: Any) -> DiagnosisMode:
    if "assess_severity" in goals and "diagnose_fault" not in goals:
        return "health_assessment"
    if "diagnose_fault" in goals:
        return "fault_diagnosis"
    if "explain_fault_code" in goals and requests_runtime_status(decision):
        return "alarm_triage"
    if requests_runtime_status(decision):
        return "alarm_triage"
    return "unknown"


def _needs_device(mode: str) -> bool:
    return mode in {"alarm_triage", "fault_diagnosis", "root_cause_analysis", "health_assessment"}


def _needs_runtime(mode: str, decision: Any) -> bool:
    return mode in {"fault_diagnosis", "root_cause_analysis", "health_assessment"} or requests_runtime_status(decision)


def _needs_manual_reference(mode: str, has_alarm_or_fault: bool) -> bool:
    return mode in {"alarm_triage", "fault_diagnosis", "root_cause_analysis"} and has_alarm_or_fault


def _needs_alarm_or_fault(mode: str) -> bool:
    return mode in {"alarm_triage", "fault_diagnosis", "root_cause_analysis"}


def _has_device_context(objects: dict[str, Any], context: dict[str, Any]) -> bool:
    inherited = _to_dict(context.get("inherited_slots"))
    return bool(objects.get("device_ids") or inherited.get("equipment_id") or context.get("active_asset_ids"))


def _bool_dict(value: Any) -> dict[str, bool]:
    return {str(key): bool(item) for key, item in _to_dict(value).items()}


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if str(item or "").strip()))
