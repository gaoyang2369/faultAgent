"""Goal-native readiness for workorder and action requests."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..workflow.axes import goal_types, requests_action_or_workorder

WORKORDER_ACTION_READINESS_SCHEMA_VERSION = "workorder_action_readiness.v2"

ActionType = Literal["workorder_decision", "workorder_draft", "device_action", "unknown"]


class WorkorderActionReadiness(BaseModel):
    schema_version: str = WORKORDER_ACTION_READINESS_SCHEMA_VERSION
    ready_for_draft: bool = False
    action_type: ActionType = "unknown"
    requires_human_confirmation: bool = True
    permission_check_required: bool = True
    risk_check_required: bool = True
    audit_log_required: bool = True
    output_guardrail_required: bool = True
    stale_refresh_required: bool = False
    missing_critical_evidence: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


def build_workorder_action_readiness(*, decision: Any) -> WorkorderActionReadiness:
    """Build readiness from GoalSet/context/evidence axes."""

    action_type = classify_action_type(decision)
    context = _to_dict(getattr(decision, "resolved_context", {}) or {})
    nodes = _bool_dict(getattr(decision, "enabled_nodes", {}) or {})
    stale_refresh_required = bool(
        context.get("stale_evidence")
        or getattr(decision, "should_refresh_runtime_data", False)
        or "latest_realtime_status" in set(_strings(getattr(decision, "missing_or_stale_evidence", []) or []))
    )
    missing = _missing_evidence(decision, action_type=action_type, stale_refresh_required=stale_refresh_required)
    blockers: list[str] = []
    if action_type == "unknown":
        blockers.append("not_workorder_or_action")
    if action_type == "device_action":
        blockers.append("device_action_direct_execution_denied")
    if context.get("relation_to_previous") == "ambiguous" or getattr(decision, "relation_to_previous", "") == "ambiguous":
        blockers.append("blocked_context_relation:ambiguous")
    authorization = _to_dict(getattr(decision, "authorization", {}) or {})
    if authorization.get("mode") in {"deny", "clarify"}:
        blockers.append("authorization_not_allow")
    for node in ("permission_check", "risk_check", "audit_log"):
        if not nodes.get(node) and requests_action_or_workorder(decision):
            blockers.append(f"{node}_required")
    if missing:
        blockers.append("missing_critical_evidence")
    if stale_refresh_required:
        blockers.append("stale_refresh_required")
    return WorkorderActionReadiness(
        ready_for_draft=action_type in {"workorder_decision", "workorder_draft"} and not blockers,
        action_type=action_type,
        requires_human_confirmation=action_type != "unknown",
        stale_refresh_required=stale_refresh_required,
        missing_critical_evidence=missing,
        blockers=_dedupe(blockers),
    )


def summarize_workorder_action_readiness(value: Any) -> dict[str, Any]:
    data = value.model_dump(exclude_none=True) if isinstance(value, WorkorderActionReadiness) else _to_dict(value)
    if not data:
        return {}
    return {
        "ready_for_draft": bool(data.get("ready_for_draft", False)),
        "action_type": data.get("action_type", "unknown"),
        "requires_human_confirmation": bool(data.get("requires_human_confirmation", True)),
        "permission_check_required": bool(data.get("permission_check_required", True)),
        "risk_check_required": bool(data.get("risk_check_required", True)),
        "audit_log_required": bool(data.get("audit_log_required", True)),
        "output_guardrail_required": bool(data.get("output_guardrail_required", True)),
        "stale_refresh_required": bool(data.get("stale_refresh_required", False)),
        "missing_critical_evidence_count": len(list(data.get("missing_critical_evidence") or [])),
        "blocker_count": len(list(data.get("blockers") or [])),
    }


def classify_action_type(decision: Any) -> ActionType:
    goals = set(goal_types(decision))
    text = " ".join(
        _strings(
            [
                getattr(decision, "action_type", "") or "",
                getattr(decision, "action_target", "") or "",
                getattr(decision, "user_goal", "") or "",
                str(getattr(decision, "task_family", "") or ""),
            ]
        )
    )
    if any(word in text for word in ("reset", "restart", "stop", "shutdown", "parameter", "config", "复位", "重启", "停机", "关闭", "参数", "配置", "修改")):
        return "device_action"
    if "create_workorder_draft" in goals:
        return "workorder_draft"
    if "decide_workorder" in goals or str(getattr(decision, "action_target", "") or "") == "workorder":
        return "workorder_decision"
    if requests_action_or_workorder(decision):
        return "device_action"
    return "unknown"


def _missing_evidence(decision: Any, *, action_type: ActionType, stale_refresh_required: bool) -> list[str]:
    missing = list(_strings(getattr(decision, "missing_slots", []) or []))
    missing.extend(_strings(getattr(decision, "missing_or_stale_evidence", []) or []))
    if action_type in {"workorder_decision", "workorder_draft"}:
        required = {"diagnosis_summary", "severity_or_status_level", "key_evidence", "recommended_action_policy"}
        satisfied = set(_strings(getattr(decision, "satisfied_evidence", []) or []))
        missing.extend(sorted(required - satisfied))
    if action_type == "device_action":
        missing.extend(["human_approval", "safe_state", "execution_permission"])
    if stale_refresh_required:
        missing.append("latest_realtime_status")
    return _dedupe(missing)


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
