"""Phase 4.3 planner gate for read-only execution preview."""

from __future__ import annotations

from typing import Any

from fault_diagnosis import config

from ..contracts import SingleAgentLimits
from .gate_contracts import PlannerGateDecision

_SEVERITY_RANK = {"none": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
_LOW_RISK_TASK_FAMILIES = {"knowledge_lookup", "runtime_status", "reporting"}
_READ_ONLY_NODE_BY_FAMILY = {
    "knowledge_lookup": {"knowledge"},
    "runtime_status": {"sql"},
    "reporting": {"report"},
}
_READ_ONLY_TOOLS_BY_NODE = {
    "knowledge": {"query_knowledge_base"},
    "sql": {"sql_db_query_checker", "sql_db_query"},
    "report": {"save_report"},
}
_BLOCKED_GOAL_TYPES = {"decide_workorder", "diagnose_fault"}
_BLOCKED_RISK_LEVELS = {"high_risk", "requires_confirmation", "write_action"}
_BLOCKED_RELATIONS = {"ambiguous", "action_followup"}
_SAFETY_NODES = {"permission_check", "risk_check", "audit_log", "output_guardrail", "evidence_validation"}


def build_planner_gate(
    *,
    decision: Any,
    shadow_plan: Any,
    planning_diff: Any,
    config_overrides: dict[str, Any] | None = None,
) -> PlannerGateDecision:
    """Build a deterministic gate decision without mutating execution state."""

    settings = _settings(config_overrides)
    mode = _mode(settings)
    legacy_nodes = _bool_dict(getattr(decision, "enabled_nodes", {}) or {})
    legacy_runtime_tools = _strings(getattr(decision, "runtime_tools", []) or [])
    shadow = _to_dict(shadow_plan)
    diff = _to_dict(planning_diff)
    task_family = str(getattr(decision, "task_family", "") or "")
    primary_task_type = str(getattr(decision, "primary_task_type", "") or "")
    allowed_families = set(settings["task_families"])
    required_status = list(settings["required_diff_status"])
    observed_status = str(diff.get("overall_status") or "")
    observed_severity = str(diff.get("severity") or "")
    shadow_tools = _shadow_authorized_tools(shadow)
    allowed_runtime_tools = _dedupe([tool for tool in legacy_runtime_tools if tool in SingleAgentLimits().allowed_tools])
    allowed_task_family = task_family in allowed_families and task_family in _LOW_RISK_TASK_FAMILIES
    blockers: list[str] = []
    reasons: list[str] = []

    if mode == "disabled":
        blockers.append("planner_gate_disabled")
    if not allowed_task_family:
        blockers.append("unsupported_task_family")
    if task_family == "action_or_workorder" or primary_task_type == "action_request" or getattr(decision, "action_type", None):
        blockers.append("action_or_workorder_not_migrated")
    decision_risk = str(getattr(decision, "risk_level", "") or "")
    if decision_risk in _BLOCKED_RISK_LEVELS:
        blockers.append(f"risk_not_migrated:{decision_risk}")
    _extend_blockers(blockers, _goal_blockers(getattr(decision, "goal_set", {}) or {}, getattr(decision, "goals", []) or []))
    relation = str((getattr(decision, "resolved_context", {}) or {}).get("relation_to_previous") or getattr(decision, "relation_to_previous", "") or "")
    if relation in _BLOCKED_RELATIONS:
        blockers.append(f"blocked_context_relation:{relation}")
    if observed_status not in required_status:
        blockers.append("diff_status_not_allowed")
    if _SEVERITY_RANK.get(observed_severity, 99) > _SEVERITY_RANK.get(str(settings["max_diff_severity"]), 2):
        blockers.append("diff_severity_too_high")
    if int((diff.get("counters") or {}).get("critical_count") or diff.get("critical_count") or 0) > 0:
        blockers.append("critical_diff_present")
    if not set(shadow_tools).issubset(set(legacy_runtime_tools)):
        blockers.append("tool_scope_violation")
    if _removes_safety_node(legacy_nodes, _shadow_enabled_nodes(shadow)):
        blockers.append("safety_node_removed")
    if _stale_workorder_blocked(decision, shadow):
        blockers.append("stale_workorder_not_migrated")
    if _unauthorized_or_missing_auth(decision):
        blockers.append("unauthorized_or_missing_auth_context")
    if _explicit_switch_reuses_artifact(decision):
        blockers.append("explicit_device_switch_reuses_artifact")

    final_nodes = _final_enabled_nodes(task_family, legacy_nodes, _shadow_enabled_nodes(shadow))
    family_tool_allowlist = _tools_for_nodes(final_nodes)
    final_runtime_tools = _dedupe(
        [
            tool
            for tool in shadow_tools
            if tool in legacy_runtime_tools
            and tool in allowed_runtime_tools
            and tool in family_tool_allowlist
        ]
    )
    if allowed_task_family:
        reasons.append("task_family_allowed_for_read_only_preview")
    if not final_runtime_tools and final_nodes:
        blockers.append("empty_final_runtime_tools")
    eligible = not blockers
    selected_source = "planner_gated" if eligible and mode == "active" else "legacy_policy"
    return PlannerGateDecision(
        mode=mode,
        eligible=eligible,
        selected_execution_source=selected_source,
        allowed_task_family=allowed_task_family,
        task_family=task_family,
        primary_task_type=primary_task_type,
        reasons=_dedupe(reasons),
        blockers=_dedupe(blockers),
        required_diff_status=required_status,
        observed_diff_status=observed_status,
        observed_diff_severity=observed_severity,
        allowed_runtime_tools=allowed_runtime_tools,
        planner_runtime_tools=shadow_tools,
        final_runtime_tools=final_runtime_tools if selected_source == "planner_gated" else legacy_runtime_tools,
        final_enabled_nodes=sorted(final_nodes) if selected_source == "planner_gated" else sorted(node for node, enabled in legacy_nodes.items() if enabled),
        fallback_to_legacy=selected_source != "planner_gated",
        safety_summary={
            "critical_count": int((diff.get("counters") or {}).get("critical_count") or diff.get("critical_count") or 0),
            "shadow_tools_subset_legacy": set(shadow_tools).issubset(set(legacy_runtime_tools)),
            "safety_nodes_preserved": not _removes_safety_node(legacy_nodes, _shadow_enabled_nodes(shadow)),
            "dry_run": mode == "dry_run",
        },
    )


def summarize_planner_gate(value: Any) -> dict[str, Any]:
    data = value.model_dump(exclude_none=True) if isinstance(value, PlannerGateDecision) else dict(value or {}) if isinstance(value, dict) else {}
    if not data:
        return {}
    return {
        "mode": data.get("mode", "disabled"),
        "eligible": bool(data.get("eligible", False)),
        "selected_execution_source": data.get("selected_execution_source", "legacy_policy"),
        "blockers": list(data.get("blockers") or [])[:8],
        "reasons": list(data.get("reasons") or [])[:8],
        "final_enabled_nodes": list(data.get("final_enabled_nodes") or []),
        "final_runtime_tools": list(data.get("final_runtime_tools") or []),
        "fallback_to_legacy": bool(data.get("fallback_to_legacy", True)),
    }


def apply_planner_gate_to_decision(decision: Any, gate: PlannerGateDecision) -> Any:
    """Apply only the active, eligible, read-only projection."""

    if gate.selected_execution_source != "planner_gated":
        return decision
    final_nodes = set(gate.final_enabled_nodes)
    decision.enabled_nodes = {node: node in final_nodes for node in dict(getattr(decision, "enabled_nodes", {}) or {})}
    for node in final_nodes:
        decision.enabled_nodes.setdefault(node, True)
    decision.runtime_tools = list(gate.final_runtime_tools)
    return decision


def _settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    overrides = dict(overrides or {})
    return {
        "enabled": bool(overrides.get("enabled", config.ENABLE_PLANNER_GATED_EXECUTION)),
        "dry_run": bool(overrides.get("dry_run", config.PLANNER_GATED_DRY_RUN)),
        "task_families": list(overrides.get("task_families", config.PLANNER_GATED_TASK_FAMILIES)),
        "required_diff_status": list(overrides.get("required_diff_status", config.PLANNER_GATED_REQUIRE_DIFF_STATUS)),
        "max_diff_severity": str(overrides.get("max_diff_severity", config.PLANNER_GATED_MAX_DIFF_SEVERITY) or "warning"),
    }


def _mode(settings: dict[str, Any]) -> str:
    if not settings["enabled"]:
        return "disabled"
    return "dry_run" if settings["dry_run"] else "active"


def _goal_blockers(goal_set: dict[str, Any], goals_value: Any) -> list[str]:
    blockers: list[str] = []
    goals = goal_set.get("goals") if isinstance(goal_set, dict) else goals_value
    for goal in goals or []:
        data = _to_dict(goal)
        goal_type = str(data.get("goal_type") or "")
        risk = str(data.get("risk_level") or "")
        if goal_type in _BLOCKED_GOAL_TYPES:
            blockers.append("action_or_workorder_not_migrated" if goal_type == "decide_workorder" else "diagnosis_not_migrated")
        if risk in _BLOCKED_RISK_LEVELS:
            blockers.append(f"risk_not_migrated:{risk}")
    return blockers


def _final_enabled_nodes(task_family: str, legacy_nodes: dict[str, bool], shadow_nodes: set[str]) -> set[str]:
    allowed_nodes = _READ_ONLY_NODE_BY_FAMILY.get(task_family, set()) | _SAFETY_NODES
    return {node for node in shadow_nodes if node in allowed_nodes and legacy_nodes.get(node)}


def _tools_for_nodes(nodes: set[str]) -> set[str]:
    tools: set[str] = set()
    for node in nodes:
        tools.update(_READ_ONLY_TOOLS_BY_NODE.get(node, set()))
    return tools


def _shadow_authorized_tools(shadow: dict[str, Any]) -> list[str]:
    return _strings((_to_dict(shadow.get("tool_plan"))).get("authorized_runtime_tools"))


def _shadow_enabled_nodes(shadow: dict[str, Any]) -> set[str]:
    nodes: set[str] = set()
    for item in shadow.get("nodes") or []:
        data = _to_dict(item)
        if data.get("desired_state") == "enabled" and data.get("node"):
            nodes.add(str(data["node"]))
    return nodes


def _removes_safety_node(legacy_nodes: dict[str, bool], shadow_nodes: set[str]) -> bool:
    return any(legacy_nodes.get(node) and node not in shadow_nodes for node in _SAFETY_NODES)


def _stale_workorder_blocked(decision: Any, shadow: dict[str, Any]) -> bool:
    context = getattr(decision, "resolved_context", {}) or {}
    if not context.get("stale_evidence"):
        return False
    output = _to_dict(shadow.get("output_plan"))
    return output.get("expected_output") == "workorder_decision" or str(getattr(decision, "task_family", "")) == "action_or_workorder"


def _unauthorized_or_missing_auth(decision: Any) -> bool:
    auth = getattr(decision, "authorization", {}) or {}
    context = getattr(decision, "resolved_context", {}) or {}
    reason = str(context.get("context_resolution_reason") or "")
    return bool(
        not auth
        or not auth.get("mode")
        or auth.get("mode") in {"deny", "clarify", "degrade"}
        or auth.get("denied_reason_code")
        or "授权范围" in reason
        or "authorization" in reason.lower()
    )


def _explicit_switch_reuses_artifact(decision: Any) -> bool:
    context = getattr(decision, "resolved_context", {}) or {}
    relation = str(context.get("relation_to_previous") or getattr(decision, "relation_to_previous", "") or "")
    return relation in {"new_case", "correction"} and bool(context.get("referenced_artifact_id"))


def _extend_blockers(blockers: list[str], values: list[str]) -> None:
    for item in values:
        if item:
            blockers.append(item)


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
