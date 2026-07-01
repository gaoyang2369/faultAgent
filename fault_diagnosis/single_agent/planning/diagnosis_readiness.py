"""Diagnosis readiness for Phase 4.4 dry-run and limited active phases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DIAGNOSIS_READINESS_SCHEMA_VERSION = "diagnosis_readiness.v1"

DiagnosisMode = Literal[
    "alarm_triage",
    "fault_diagnosis",
    "root_cause_analysis",
    "health_assessment",
    "unknown",
]
RecommendedNextPhase = Literal["keep_legacy", "more_eval", "candidate_for_limited_active"]
DiagnosisActiveMode = Literal["disabled", "dry_run", "limited_explanation"]

_DIAGNOSIS_TASK_TYPES = {
    "alarm_triage",
    "fault_diagnosis",
    "root_cause_analysis",
    "health_assessment",
}
_SQL_TOOLS = {"sql_db_query_checker", "sql_db_query"}
_KNOWLEDGE_TOOLS = {"query_knowledge_base"}
_STRICT_MODES = {"root_cause_analysis", "health_assessment"}
_SEVERITY_RANK = {"none": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}


class DiagnosisReadiness(BaseModel):
    schema_version: str = DIAGNOSIS_READINESS_SCHEMA_VERSION
    ready_for_active: bool = False
    active_allowed: bool = False
    active_mode: DiagnosisActiveMode = "disabled"
    active_scope: list[str] = Field(default_factory=list)
    active_blockers: list[str] = Field(default_factory=list)
    evidence_complete: bool = False
    has_runtime_status: bool = False
    has_manual_reference: bool = False
    has_alarm_or_fault_context: bool = False
    claims_have_supporting_evidence: bool = False
    stale_evidence_disclosed: bool = False
    missing_critical_evidence: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    diagnosis_mode: DiagnosisMode = "unknown"
    recommended_next_phase: RecommendedNextPhase = "keep_legacy"


def build_diagnosis_readiness(
    *,
    decision: Any,
    shadow_plan: Any,
    planning_diff: Any,
) -> DiagnosisReadiness:
    """Build a compact readiness assessment that never authorizes active diagnosis."""

    shadow = _to_dict(shadow_plan)
    diff = _to_dict(planning_diff)
    context = _to_dict(getattr(decision, "resolved_context", {}) or {})
    evidence_plan = _to_dict(shadow.get("evidence_plan"))
    output_plan = _to_dict(shadow.get("output_plan"))
    objects = _to_dict(getattr(decision, "objects", {}) or {})
    legacy_nodes = _bool_dict(getattr(decision, "enabled_nodes", {}) or {})
    shadow_nodes = _shadow_enabled_nodes(shadow)
    runtime_tools = set(_strings(getattr(decision, "runtime_tools", []) or []))
    shadow_tools = set(_strings(_to_dict(shadow.get("tool_plan")).get("authorized_runtime_tools")))
    required_evidence = set(_strings(evidence_plan.get("required_evidence")))
    missing_evidence = set(_strings(evidence_plan.get("missing_evidence")))
    disclosures = set(
        _strings(evidence_plan.get("disclosure_required"))
        + _strings(output_plan.get("required_disclosures"))
        + _strings(getattr(decision, "missing_or_stale_evidence", []) or [])
    )
    mode = _diagnosis_mode(getattr(decision, "primary_task_type", ""))

    inherited = _to_dict(context.get("inherited_slots"))
    stale = bool(context.get("stale_evidence"))
    stale_disclosed = not stale or bool(
        disclosures.intersection({"evidence_stale", "fresh_runtime_status_required", "latest_realtime_status"})
        or getattr(decision, "should_refresh_runtime_data", False)
    )
    has_runtime = bool(
        shadow_nodes.intersection({"sql"})
        or legacy_nodes.get("sql")
        or runtime_tools.intersection(_SQL_TOOLS)
        or (inherited.get("evidence_bundle") and not stale)
        or (
            context.get("referenced_artifact_id")
            and getattr(decision, "evidence_mode", "") == "reuse_previous_artifact"
            and not stale
        )
    )
    has_manual = bool(
        shadow_nodes.intersection({"knowledge"})
        or legacy_nodes.get("knowledge")
        or runtime_tools.intersection(_KNOWLEDGE_TOOLS)
    )
    has_alarm_or_fault = bool(
        objects.get("alarm_codes")
        or context.get("active_fault_codes")
        or inherited.get("fault_codes")
        or set(_strings(getattr(decision, "intent_stack", []) or [])).intersection(
            {"explain_alarm_code", "fault_diagnosis", "severity_assessment", "resolution_recommendation"}
        )
    )
    has_analysis = bool(shadow_nodes.intersection({"analysis"}) or legacy_nodes.get("analysis"))
    claims_have_support = _claims_have_supporting_evidence(
        missing_evidence=missing_evidence,
        required_evidence=required_evidence,
        shadow_nodes=shadow_nodes,
        legacy_nodes=legacy_nodes,
    )
    missing_slots = set(_strings(getattr(decision, "missing_slots", []) or []))
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
    if not has_analysis:
        blockers.append("missing_analysis_basis")
        missing.append("analysis_basis")
    if missing_slots.intersection({"device", "asset_context", "asset_or_group_context", "event_or_asset_context"}):
        blockers.append("missing_device")
        missing.append("device")
    if context.get("relation_to_previous") == "ambiguous":
        blockers.append("ambiguous_context")
    if not stale_disclosed:
        blockers.append("stale_evidence_without_disclosure")
        missing.append("freshness_disclosure")
    if _unauthorized_context(decision, context):
        blockers.append("unauthorized_inherited_artifact")
    if str(diff.get("overall_status") or "") == "unsafe_mismatch":
        blockers.append("planning_diff_unsafe_mismatch")
    if int((_to_dict(diff.get("counters")).get("critical_count") or diff.get("critical_count") or 0) or 0) > 0:
        blockers.append("planning_diff_critical")
    if _SEVERITY_RANK.get(str(diff.get("severity") or "none"), 99) >= _SEVERITY_RANK["critical"]:
        blockers.append("planning_diff_critical")
    if not shadow_tools.issubset(runtime_tools):
        blockers.append("shadow_tools_exceed_legacy_runtime_tools")
    if _would_skip_safety_node(legacy_nodes, shadow_nodes):
        blockers.append("diagnosis_would_skip_safety_node")
    if not claims_have_support:
        blockers.append("claims_without_supporting_evidence")
        missing.append("claim_supporting_evidence")
    if missing_evidence and not disclosures:
        blockers.append("missing_evidence_not_disclosed")
    if missing and not disclosures and not stale_disclosed:
        blockers.append("conclusion_without_evidence_disclosure")

    missing = _dedupe([*missing, *missing_evidence])
    blockers = _dedupe(blockers)
    evidence_complete = bool(
        not blockers
        and has_runtime
        and has_analysis
        and (has_manual or not _needs_manual_reference(mode, has_alarm_or_fault))
        and (has_alarm_or_fault or not _needs_alarm_or_fault(mode))
        and not missing
    )
    recommended: RecommendedNextPhase
    if blockers:
        recommended = "keep_legacy"
    elif mode in _STRICT_MODES:
        recommended = "more_eval"
    elif evidence_complete:
        recommended = "candidate_for_limited_active"
    else:
        recommended = "more_eval"

    return DiagnosisReadiness(
        ready_for_active=False,
        evidence_complete=evidence_complete,
        has_runtime_status=has_runtime,
        has_manual_reference=has_manual,
        has_alarm_or_fault_context=has_alarm_or_fault,
        claims_have_supporting_evidence=claims_have_support,
        stale_evidence_disclosed=stale_disclosed,
        missing_critical_evidence=missing,
        blocked_reasons=blockers,
        diagnosis_mode=mode,
        recommended_next_phase=recommended,
    )


def summarize_diagnosis_readiness(value: Any) -> dict[str, Any]:
    data = value.model_dump(exclude_none=True) if isinstance(value, DiagnosisReadiness) else _to_dict(value)
    if not data:
        return {}
    missing = list(data.get("missing_critical_evidence") or [])
    blocked = list(data.get("blocked_reasons") or [])
    return {
        "diagnosis_mode": data.get("diagnosis_mode", "unknown"),
        "ready_for_active": bool(data.get("ready_for_active", False)),
        "active_allowed": bool(data.get("active_allowed", False)),
        "active_mode": data.get("active_mode", "disabled"),
        "active_scope": list(data.get("active_scope") or []),
        "active_blocker_count": len(list(data.get("active_blockers") or [])),
        "missing_critical_evidence_count": len(missing),
        "recommended_next_phase": data.get("recommended_next_phase", "keep_legacy"),
    }


def _diagnosis_mode(value: Any) -> DiagnosisMode:
    text = str(value or "").strip()
    return text if text in _DIAGNOSIS_TASK_TYPES else "unknown"


def _needs_device(mode: str) -> bool:
    return mode in {"alarm_triage", "fault_diagnosis", "root_cause_analysis", "health_assessment"}


def _needs_runtime(mode: str, decision: Any) -> bool:
    if mode in {"fault_diagnosis", "root_cause_analysis", "health_assessment"}:
        return True
    if mode == "alarm_triage":
        intents = set(_strings(getattr(decision, "intent_stack", []) or []))
        return bool(intents.intersection({"check_current_status", "severity_assessment", "resolution_recommendation"}))
    return False


def _needs_manual_reference(mode: str, has_alarm_or_fault: bool) -> bool:
    if mode in {"alarm_triage", "fault_diagnosis", "root_cause_analysis"}:
        return True
    return has_alarm_or_fault


def _needs_alarm_or_fault(mode: str) -> bool:
    return mode in {"alarm_triage", "fault_diagnosis", "root_cause_analysis"}


def _has_device_context(objects: dict[str, Any], context: dict[str, Any]) -> bool:
    inherited = _to_dict(context.get("inherited_slots"))
    return bool(objects.get("device_ids") or context.get("active_asset") or inherited.get("device"))


def _unauthorized_context(decision: Any, context: dict[str, Any]) -> bool:
    auth = _to_dict(getattr(decision, "authorization", {}) or {})
    reason = str(context.get("context_resolution_reason") or "")
    return bool(
        not auth
        or auth.get("mode") in {"deny", "clarify", "degrade"}
        or auth.get("denied_reason_code")
        or "授权范围" in reason
        or "authorization" in reason.lower()
    )


def _would_skip_safety_node(legacy_nodes: dict[str, bool], shadow_nodes: set[str]) -> bool:
    for node in ("evidence_validation", "output_guardrail"):
        if legacy_nodes.get(node) and node not in shadow_nodes:
            return True
    return False


def _claims_have_supporting_evidence(
    *,
    missing_evidence: set[str],
    required_evidence: set[str],
    shadow_nodes: set[str],
    legacy_nodes: dict[str, bool],
) -> bool:
    if missing_evidence.intersection({"claim_supporting_evidence", "claims_supporting_evidence", "claims_without_supporting_evidence"}):
        return False
    if required_evidence.intersection({"diagnosis_basis", "severity_basis", "report_evidence"}):
        return True
    if shadow_nodes.intersection({"analysis", "evidence_validation"}) or legacy_nodes.get("analysis"):
        return True
    return False


def _shadow_enabled_nodes(shadow: dict[str, Any]) -> set[str]:
    nodes: set[str] = set()
    for item in shadow.get("nodes") or []:
        data = _to_dict(item)
        if data.get("desired_state") == "enabled" and data.get("node"):
            nodes.add(str(data["node"]))
    return nodes


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _bool_dict(value: Any) -> dict[str, bool]:
    return {str(key): bool(item) for key, item in _to_dict(value).items()}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
