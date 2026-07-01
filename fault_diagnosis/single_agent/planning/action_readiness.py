"""Phase 4.4R dry-run readiness for workorder and action requests."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..compat import goal_types as compat_goal_types
from ..compat import legacy_intents, legacy_task_value

WORKORDER_ACTION_READINESS_SCHEMA_VERSION = "workorder_action_readiness.v1"

ActionType = Literal["workorder_decision", "workorder_draft", "device_action", "unknown"]
RecommendedNextPhase = Literal["keep_legacy", "more_eval", "candidate_for_draft_only"]

_DEVICE_ACTION_WORDS = (
    "reset",
    "restart",
    "stop",
    "start",
    "shutdown",
    "parameter",
    "config",
    "复位",
    "重启",
    "停机",
    "启动",
    "启机",
    "关闭",
    "参数",
    "配置",
    "修改",
)
_WORKORDER_DRAFT_WORDS = ("draft", "草稿")
_WORKORDER_DECISION_WORDS = ("need", "decide", "whether", "是否", "要不要", "需不需要", "判断")
_FORBIDDEN_OUTPUT_WORDS = (
    "executed",
    "dispatched",
    "applied",
    "reset_done",
    "closed",
    "stopped",
    "parameter_changed",
    "已执行",
    "已派发",
    "已下发",
    "已复位",
    "已重启",
    "已停机",
    "已关闭",
    "已修改参数",
)


class WorkorderActionReadiness(BaseModel):
    schema_version: str = WORKORDER_ACTION_READINESS_SCHEMA_VERSION
    ready_for_active: bool = False
    dry_run_only: bool = True
    action_type: ActionType = "unknown"
    requires_human_confirmation: bool = True
    permission_check_required: bool = True
    risk_check_required: bool = True
    audit_log_required: bool = True
    output_guardrail_required: bool = True
    stale_refresh_required: bool = False
    missing_critical_evidence: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    recommended_next_phase: RecommendedNextPhase = "keep_legacy"


def build_workorder_action_readiness(
    *,
    decision: Any,
    shadow_plan: Any,
    planning_diff: Any,
) -> WorkorderActionReadiness:
    """Build a compact dry-run-only readiness contract for high-risk requests."""

    shadow = _to_dict(shadow_plan)
    diff = _to_dict(planning_diff)
    context = _to_dict(getattr(decision, "resolved_context", {}) or {})
    legacy_nodes = _bool_dict(getattr(decision, "enabled_nodes", {}) or {})
    shadow_nodes = _shadow_enabled_nodes(shadow)
    action_type = classify_action_type(decision, shadow)
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
        blockers.append("device_action_not_migrated")
    if action_type in {"workorder_decision", "workorder_draft"}:
        blockers.append("workorder_action_dry_run_only")
    if str(diff.get("overall_status") or "") not in {"aligned", "acceptable_diff"}:
        blockers.append("diff_status_not_allowed")
    if int((_to_dict(diff.get("counters")).get("critical_count") or diff.get("critical_count") or 0) or 0) > 0:
        blockers.append("critical_diff_present")
    if _severity_rank(str(diff.get("severity") or "none")) >= _severity_rank("critical"):
        blockers.append("critical_diff_present")
    if context.get("relation_to_previous") == "ambiguous" or getattr(decision, "relation_to_previous", "") == "ambiguous":
        blockers.append("blocked_context_relation:ambiguous")
    if context.get("relation_to_previous") == "action_followup" or getattr(decision, "relation_to_previous", "") == "action_followup":
        blockers.append("blocked_context_relation:action_followup")
    if _unauthorized_or_missing_auth(decision):
        blockers.append("unauthorized_or_missing_auth_context")
    if stale_refresh_required and not (
        getattr(decision, "should_refresh_runtime_data", False)
        or _has_freshness_disclosure(shadow)
    ):
        blockers.append("stale_refresh_or_disclosure_required")
    if missing:
        blockers.append("missing_critical_evidence")
    if not legacy_nodes.get("permission_check"):
        blockers.append("permission_check_required")
    if not legacy_nodes.get("risk_check"):
        blockers.append("risk_check_required")
    if not legacy_nodes.get("audit_log"):
        blockers.append("audit_log_required")
    if not legacy_nodes.get("output_guardrail"):
        blockers.append("output_guardrail_required")
    if _would_remove_required_node(legacy_nodes, shadow_nodes, "permission_check"):
        blockers.append("permission_check_would_be_removed")
    if _would_remove_required_node(legacy_nodes, shadow_nodes, "risk_check"):
        blockers.append("risk_check_would_be_removed")
    if _would_remove_required_node(legacy_nodes, shadow_nodes, "audit_log"):
        blockers.append("audit_log_would_be_removed")
    if _would_remove_required_node(legacy_nodes, shadow_nodes, "output_guardrail"):
        blockers.append("output_guardrail_would_be_removed")
    if _unsafe_output_semantics(shadow):
        blockers.append("unsafe_action_completion_semantics")

    recommended: RecommendedNextPhase = "keep_legacy"
    if action_type in {"workorder_decision", "workorder_draft"} and not missing:
        recommended = "candidate_for_draft_only"
    elif action_type != "device_action":
        recommended = "more_eval"

    return WorkorderActionReadiness(
        ready_for_active=False,
        dry_run_only=True,
        action_type=action_type,
        requires_human_confirmation=True,
        permission_check_required=True,
        risk_check_required=True,
        audit_log_required=True,
        output_guardrail_required=True,
        stale_refresh_required=stale_refresh_required,
        missing_critical_evidence=missing,
        blockers=_dedupe(blockers),
        recommended_next_phase=recommended,
    )


def summarize_workorder_action_readiness(value: Any) -> dict[str, Any]:
    data = value.model_dump(exclude_none=True) if isinstance(value, WorkorderActionReadiness) else _to_dict(value)
    if not data:
        return {}
    return {
        "ready_for_active": bool(data.get("ready_for_active", False)),
        "dry_run_only": bool(data.get("dry_run_only", True)),
        "action_type": data.get("action_type", "unknown"),
        "requires_human_confirmation": bool(data.get("requires_human_confirmation", True)),
        "permission_check_required": bool(data.get("permission_check_required", True)),
        "risk_check_required": bool(data.get("risk_check_required", True)),
        "audit_log_required": bool(data.get("audit_log_required", True)),
        "output_guardrail_required": bool(data.get("output_guardrail_required", True)),
        "stale_refresh_required": bool(data.get("stale_refresh_required", False)),
        "missing_critical_evidence_count": len(list(data.get("missing_critical_evidence") or [])),
        "blocker_count": len(list(data.get("blockers") or [])),
        "recommended_next_phase": data.get("recommended_next_phase", "keep_legacy"),
    }


def classify_action_type(decision: Any, shadow_plan: Any | None = None) -> ActionType:
    primary = legacy_task_value(decision, default="")
    task_family = str(getattr(decision, "task_family", "") or "")
    action_text = " ".join(
        _strings(
            [
                primary,
                task_family,
                getattr(decision, "action_type", "") or "",
                getattr(decision, "action_target", "") or "",
                getattr(decision, "user_goal", "") or "",
                *legacy_intents(decision),
            ]
        )
    )
    goal_types = set(compat_goal_types(decision))
    shadow = _to_dict(shadow_plan)
    expected_output = str(_to_dict(shadow.get("output_plan")).get("expected_output") or "")
    shadow_nodes = _shadow_enabled_nodes(shadow)

    if any(word in action_text for word in _DEVICE_ACTION_WORDS):
        return "device_action"
    if "create_workorder_draft" in goal_types or "workorder_draft" in expected_output:
        return "workorder_draft"
    if any(word in action_text for word in _WORKORDER_DRAFT_WORDS) and ("workorder" in action_text or "工单" in action_text):
        return "workorder_draft"
    if "decide_workorder" in goal_types or "workorder_decision" in expected_output or "workorder_decision" in shadow_nodes:
        return "workorder_decision"
    if primary == "action_request":
        return "device_action"
    if task_family == "action_or_workorder":
        if any(word in action_text for word in _WORKORDER_DECISION_WORDS) or "workorder" in action_text or "工单" in action_text:
            return "workorder_decision"
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


def _has_freshness_disclosure(shadow: dict[str, Any]) -> bool:
    evidence_plan = _to_dict(shadow.get("evidence_plan"))
    output_plan = _to_dict(shadow.get("output_plan"))
    disclosures = set(_strings(evidence_plan.get("disclosure_required")) + _strings(output_plan.get("required_disclosures")))
    return bool(disclosures.intersection({"evidence_stale", "fresh_runtime_status_required", "latest_realtime_status"}))


def _unsafe_output_semantics(shadow: dict[str, Any]) -> bool:
    output = _to_dict(shadow.get("output_plan"))
    text = " ".join(
        _strings(
            [
                output.get("expected_output"),
                output.get("answer_style"),
                *list(output.get("forbidden_claims") or []),
                *list(output.get("required_disclosures") or []),
            ]
        )
    )
    return any(word in text for word in _FORBIDDEN_OUTPUT_WORDS)


def _would_remove_required_node(legacy_nodes: dict[str, bool], shadow_nodes: set[str], node: str) -> bool:
    return bool(legacy_nodes.get(node) and node not in shadow_nodes)


def _unauthorized_or_missing_auth(decision: Any) -> bool:
    auth = _to_dict(getattr(decision, "authorization", {}) or {})
    if not auth:
        return True
    return str(auth.get("mode") or "") not in {"allow", "degraded"}


def _shadow_enabled_nodes(shadow: dict[str, Any]) -> set[str]:
    nodes: set[str] = set()
    for node in shadow.get("nodes") or []:
        data = _to_dict(node)
        if data.get("enabled", True) is not False:
            name = str(data.get("node") or data.get("name") or "")
            if name:
                nodes.add(name)
    return nodes


def _bool_dict(value: Any) -> dict[str, bool]:
    return {str(key): bool(val) for key, val in dict(value or {}).items()}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return value
    return {}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _severity_rank(value: str) -> int:
    return {"none": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}.get(value, 99)
