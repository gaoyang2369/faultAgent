"""Phase 4.3 planner gate for read-only execution preview."""

from __future__ import annotations

from typing import Any

from fault_diagnosis import config

from ..compat import legacy_intents, planner_gate_task_fields_for_compat, route_is_action_or_workorder
from ..contracts import SingleAgentLimits
from .action_readiness import build_workorder_action_readiness, summarize_workorder_action_readiness
from .diagnosis_readiness import build_diagnosis_readiness, summarize_diagnosis_readiness
from .gate_contracts import PlannerGateDecision
from .manual_confirmation import build_manual_confirmation_requirement, summarize_manual_confirmation_requirement

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
_DIAGNOSIS_ACTIVE_NODES = {"sql", "knowledge", "analysis", "resolution_recommendation", "report"}
_DIAGNOSIS_STRICT_BLOCKED_MODES = {
    "root_cause_analysis": "root_cause_analysis_not_migrated",
    "health_assessment": "health_assessment_not_migrated",
}
_BLOCKED_GOAL_TYPES = {"decide_workorder", "diagnose_fault"}
_BLOCKED_RISK_LEVELS = {"high_risk", "requires_confirmation", "write_action"}
_BLOCKED_RELATIONS = {"ambiguous", "action_followup"}
_SAFETY_NODES = {"permission_check", "risk_check", "audit_log", "output_guardrail", "evidence_validation"}
_DANGEROUS_OUTPUT_WORDS = (
    "executed",
    "dispatched",
    "applied",
    "reset_done",
    "已执行",
    "已派发",
    "已下发",
    "已复位",
    "已重启",
    "已停机",
    "已关闭",
    "已修改",
)
_HIGH_RISK_ACTION_WORDS = (
    "workorder",
    "dispatch",
    "reset",
    "restart",
    "stop",
    "shutdown",
    "parameter",
    "config",
    "工单",
    "派单",
    "派发",
    "复位",
    "重启",
    "停机",
    "启停",
    "参数",
    "配置",
    "修改",
)


def build_planner_gate(
    *,
    decision: Any,
    shadow_plan: Any,
    planning_diff: Any,
    config_overrides: dict[str, Any] | None = None,
) -> PlannerGateDecision:
    """Build a deterministic gate decision without mutating execution state."""

    settings = _settings(config_overrides)
    legacy_nodes = _bool_dict(getattr(decision, "enabled_nodes", {}) or {})
    legacy_runtime_tools = _strings(getattr(decision, "runtime_tools", []) or [])
    shadow = _to_dict(shadow_plan)
    diff = _to_dict(planning_diff)
    task_family = str(getattr(decision, "task_family", "") or "")
    mode = _mode(settings, task_family=task_family)
    allowed_families = set(settings["task_families"])
    required_status = list(settings["required_diff_status"])
    observed_status = str(diff.get("overall_status") or "")
    observed_severity = str(diff.get("severity") or "")
    shadow_tools = _shadow_authorized_tools(shadow)
    allowed_runtime_tools = _dedupe([tool for tool in legacy_runtime_tools if tool in SingleAgentLimits().allowed_tools])
    diagnosis_dry_run = task_family == "diagnosis" and bool(settings["diagnosis_dry_run"])
    diagnosis_active_requested = task_family == "diagnosis" and mode == "active"
    allowed_task_family = (
        task_family in allowed_families
        and task_family in _LOW_RISK_TASK_FAMILIES
    ) or diagnosis_dry_run or diagnosis_active_requested
    diagnosis_readiness = None
    diagnosis_active_scope: list[str] = []
    diagnosis_active_blockers: list[str] = []
    workorder_action_readiness = None
    manual_confirmation = None
    blockers: list[str] = []
    reasons: list[str] = []

    if mode == "disabled":
        blockers.append("planner_gate_disabled")
    if not allowed_task_family:
        blockers.append("unsupported_task_family")
    if task_family == "diagnosis":
        diagnosis_readiness = build_diagnosis_readiness(
            decision=decision,
            shadow_plan=shadow_plan,
            planning_diff=planning_diff,
        )
        if diagnosis_active_requested:
            diagnosis_active_blockers = _diagnosis_active_blockers(
                decision=decision,
                shadow=shadow,
                diff=diff,
                readiness=diagnosis_readiness.model_dump(exclude_none=True),
                settings=settings,
                legacy_nodes=legacy_nodes,
                legacy_runtime_tools=legacy_runtime_tools,
                shadow_tools=shadow_tools,
            )
        elif diagnosis_dry_run:
            blockers.append("diagnosis_dry_run_only")
            if not settings["diagnosis_active"]:
                blockers.append("diagnosis_active_not_enabled")
        else:
            blockers.append("diagnosis_active_not_enabled")
        _extend_blockers(blockers, diagnosis_readiness.blocked_reasons)
        _extend_blockers(blockers, diagnosis_active_blockers)
    high_risk_request = _is_workorder_or_action(decision, shadow)
    if high_risk_request:
        workorder_action_readiness = build_workorder_action_readiness(
            decision=decision,
            shadow_plan=shadow_plan,
            planning_diff=planning_diff,
        )
        manual_confirmation = build_manual_confirmation_requirement(
            decision=decision,
            workorder_action_readiness=workorder_action_readiness,
        )
        _extend_blockers(blockers, workorder_action_readiness.blockers)
        reasons.append("workorder_action_dry_run_observation")
    if route_is_action_or_workorder(decision):
        blockers.append("action_or_workorder_not_migrated")
    decision_risk = str(getattr(decision, "risk_level", "") or "")
    if decision_risk in _BLOCKED_RISK_LEVELS:
        blockers.append(f"risk_not_migrated:{decision_risk}")
    _extend_blockers(
        blockers,
        _goal_blockers(
            getattr(decision, "goal_set", {}) or {},
            getattr(decision, "goals", []) or [],
            task_family=task_family,
        ),
    )
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

    final_nodes = _final_enabled_nodes(task_family, legacy_nodes, _shadow_enabled_nodes(shadow), decision=decision)
    if task_family == "diagnosis":
        diagnosis_active_scope = sorted(node for node in final_nodes if node in _DIAGNOSIS_ACTIVE_NODES)
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
    if diagnosis_dry_run and mode == "dry_run":
        reasons.append("diagnosis_dry_run_observation")
    if diagnosis_active_requested:
        reasons.append("diagnosis_limited_active_requested")
    if not final_runtime_tools and final_nodes:
        blockers.append("empty_final_runtime_tools")
    eligible = not blockers
    diagnosis_dry_run_eligible = bool(diagnosis_dry_run and diagnosis_readiness and not diagnosis_readiness.blocked_reasons)
    diagnosis_active_eligible = bool(diagnosis_active_requested and eligible and diagnosis_readiness)
    selected_source = (
        "planner_gated"
        if eligible and mode == "active" and (task_family != "diagnosis" or diagnosis_active_eligible)
        else "legacy_policy"
    )
    safety_summary = {
        "critical_count": int((diff.get("counters") or {}).get("critical_count") or diff.get("critical_count") or 0),
        "shadow_tools_subset_legacy": set(shadow_tools).issubset(set(legacy_runtime_tools)),
        "safety_nodes_preserved": not _removes_safety_node(legacy_nodes, _shadow_enabled_nodes(shadow)),
        "dry_run": mode == "dry_run",
    }
    if diagnosis_readiness is not None:
        if mode == "active":
            diagnosis_readiness.active_mode = "limited_explanation" if diagnosis_active_requested else "disabled"
        else:
            diagnosis_readiness.active_mode = "dry_run" if mode == "dry_run" else "disabled"
        diagnosis_readiness.active_scope = list(diagnosis_active_scope)
        diagnosis_readiness.active_blockers = _dedupe(diagnosis_active_blockers)
        diagnosis_readiness.active_allowed = selected_source == "planner_gated"
        diagnosis_readiness.ready_for_active = selected_source == "planner_gated"
        safety_summary["diagnosis_readiness"] = diagnosis_readiness.model_dump(exclude_none=True)
    if workorder_action_readiness is not None:
        safety_summary["workorder_action_readiness"] = workorder_action_readiness.model_dump(exclude_none=True)
    if manual_confirmation is not None:
        safety_summary["manual_confirmation"] = manual_confirmation.model_dump(exclude_none=True)
    return PlannerGateDecision(
        mode=mode,
        eligible=eligible,
        dry_run_eligible=diagnosis_dry_run_eligible if task_family == "diagnosis" else (eligible and mode == "dry_run"),
        selected_execution_source=selected_source,
        allowed_task_family=allowed_task_family,
        task_family=task_family,
        **planner_gate_task_fields_for_compat(decision),
        reasons=_dedupe(reasons),
        blockers=_dedupe(blockers),
        required_diff_status=required_status,
        observed_diff_status=observed_status,
        observed_diff_severity=observed_severity,
        allowed_runtime_tools=allowed_runtime_tools,
        planner_runtime_tools=shadow_tools,
        final_runtime_tools=final_runtime_tools if selected_source == "planner_gated" else legacy_runtime_tools,
        final_enabled_nodes=sorted(final_nodes) if selected_source == "planner_gated" else sorted(node for node, enabled in legacy_nodes.items() if enabled),
        active_scope=diagnosis_active_scope if selected_source == "planner_gated" else [],
        fallback_to_legacy=selected_source != "planner_gated",
        safety_summary=safety_summary,
    )


def summarize_planner_gate(value: Any) -> dict[str, Any]:
    data = value.model_dump(exclude_none=True) if isinstance(value, PlannerGateDecision) else dict(value or {}) if isinstance(value, dict) else {}
    if not data:
        return {}
    return {
        "mode": data.get("mode", "disabled"),
        "eligible": bool(data.get("eligible", False)),
        "dry_run_eligible": bool(data.get("dry_run_eligible", False)),
        "selected_execution_source": data.get("selected_execution_source", "legacy_policy"),
        "blockers": list(data.get("blockers") or [])[:8],
        "reasons": list(data.get("reasons") or [])[:8],
        "final_enabled_nodes": list(data.get("final_enabled_nodes") or []),
        "final_runtime_tools": list(data.get("final_runtime_tools") or []),
        "active_scope": list(data.get("active_scope") or []),
        "fallback_to_legacy": bool(data.get("fallback_to_legacy", True)),
        "diagnosis_readiness": summarize_diagnosis_readiness(
            (_to_dict(data.get("safety_summary"))).get("diagnosis_readiness")
        ),
        "workorder_action_readiness": summarize_workorder_action_readiness(
            (_to_dict(data.get("safety_summary"))).get("workorder_action_readiness")
        ),
        "manual_confirmation": summarize_manual_confirmation_requirement(
            (_to_dict(data.get("safety_summary"))).get("manual_confirmation")
        ),
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
        "diagnosis_dry_run": bool(overrides.get("diagnosis_dry_run", config.PLANNER_GATE_DIAGNOSIS_DRY_RUN)),
        "diagnosis_active": bool(overrides.get("diagnosis_active", config.PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE)),
        "diagnosis_active_modes": list(
            overrides.get("diagnosis_active_modes", config.PLANNER_GATE_DIAGNOSIS_ACTIVE_MODES)
        ),
        "diagnosis_active_require_readiness": str(
            overrides.get(
                "diagnosis_active_require_readiness",
                config.PLANNER_GATE_DIAGNOSIS_ACTIVE_REQUIRE_READINESS,
            )
            or "candidate_for_limited_active"
        ),
        "diagnosis_active_max_diff_severity": str(
            overrides.get(
                "diagnosis_active_max_diff_severity",
                config.PLANNER_GATE_DIAGNOSIS_ACTIVE_MAX_DIFF_SEVERITY,
            )
            or "warning"
        ),
        "diagnosis_active_allow_rca": bool(
            overrides.get("diagnosis_active_allow_rca", config.PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_RCA)
        ),
        "diagnosis_active_allow_health": bool(
            overrides.get("diagnosis_active_allow_health", config.PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_HEALTH)
        ),
        "task_families": list(overrides.get("task_families", config.PLANNER_GATED_TASK_FAMILIES)),
        "required_diff_status": list(overrides.get("required_diff_status", config.PLANNER_GATED_REQUIRE_DIFF_STATUS)),
        "max_diff_severity": str(overrides.get("max_diff_severity", config.PLANNER_GATED_MAX_DIFF_SEVERITY) or "warning"),
    }


def _is_workorder_or_action(decision: Any, shadow: dict[str, Any]) -> bool:
    if route_is_action_or_workorder(decision):
        return True
    output = _to_dict(shadow.get("output_plan"))
    if str(output.get("expected_output") or "") in {"workorder_decision", "workorder_draft"}:
        return True
    if "workorder_decision" in _shadow_enabled_nodes(shadow):
        return True
    for goal in _goals_for_decision(decision):
        if str(goal.get("goal_type") or "") in {"decide_workorder", "create_workorder_draft"}:
            return True
    text = " ".join(
        _strings(
            [
                getattr(decision, "user_goal", "") or "",
                getattr(decision, "action_type", "") or "",
                getattr(decision, "action_target", "") or "",
                *legacy_intents(decision),
            ]
        )
    ).lower()
    if any(word in text for word in _HIGH_RISK_ACTION_WORDS):
        return True
    return False


def _mode(settings: dict[str, Any], *, task_family: str) -> str:
    if task_family == "diagnosis":
        if settings["enabled"] and not settings["dry_run"] and settings["diagnosis_active"]:
            return "active"
        if settings["diagnosis_dry_run"]:
            return "dry_run"
    if not settings["enabled"]:
        return "disabled"
    return "dry_run" if settings["dry_run"] else "active"


def _diagnosis_active_blockers(
    *,
    decision: Any,
    shadow: dict[str, Any],
    diff: dict[str, Any],
    readiness: dict[str, Any],
    settings: dict[str, Any],
    legacy_nodes: dict[str, bool],
    legacy_runtime_tools: list[str],
    shadow_tools: list[str],
) -> list[str]:
    blockers: list[str] = []
    mode = str(readiness.get("diagnosis_mode") or "unknown")
    allowed_modes = set(_strings(settings.get("diagnosis_active_modes")))
    relation = str((_to_dict(getattr(decision, "resolved_context", {}) or {})).get("relation_to_previous") or "")
    shadow_nodes = _shadow_enabled_nodes(shadow)
    final_nodes = _final_enabled_nodes("diagnosis", legacy_nodes, shadow_nodes, decision=decision)

    if not settings["enabled"]:
        blockers.append("planner_gate_disabled")
    if settings["dry_run"]:
        blockers.append("planner_gate_dry_run")
    if not settings["diagnosis_active"]:
        blockers.append("diagnosis_active_not_enabled")
    if mode not in allowed_modes:
        blockers.append(f"diagnosis_mode_not_allowed:{mode}")
    if mode == "root_cause_analysis" and not settings["diagnosis_active_allow_rca"]:
        blockers.append(_DIAGNOSIS_STRICT_BLOCKED_MODES["root_cause_analysis"])
    if mode == "health_assessment" and not settings["diagnosis_active_allow_health"]:
        blockers.append(_DIAGNOSIS_STRICT_BLOCKED_MODES["health_assessment"])
    if readiness.get("recommended_next_phase") != settings["diagnosis_active_require_readiness"]:
        blockers.append("diagnosis_readiness_not_candidate")
    if not readiness.get("evidence_complete"):
        blockers.append("diagnosis_evidence_incomplete")
    if _needs_runtime_for_active(decision, readiness, shadow) and not readiness.get("has_runtime_status"):
        blockers.append("missing_runtime_status")
    if _needs_manual_for_active(decision, readiness) and not readiness.get("has_manual_reference"):
        blockers.append("missing_manual_reference")
    if not readiness.get("claims_have_supporting_evidence"):
        blockers.append("claims_without_supporting_evidence")
    if not readiness.get("stale_evidence_disclosed"):
        blockers.append("stale_evidence_without_disclosure")
    if readiness.get("missing_critical_evidence"):
        blockers.append("missing_critical_evidence")
    if relation == "ambiguous":
        blockers.append("blocked_context_relation:ambiguous")
    if relation == "action_followup":
        blockers.append("blocked_context_relation:action_followup")
    if "unauthorized_inherited_artifact" in set(_strings(readiness.get("blocked_reasons"))):
        blockers.append("unauthorized_inherited_artifact")
    if _goal_has_action_or_high_risk(decision):
        blockers.append("action_or_workorder_goal_not_migrated")
    if str(diff.get("overall_status") or "") not in {"aligned", "acceptable_diff"}:
        blockers.append("diff_status_not_allowed")
    if _SEVERITY_RANK.get(str(diff.get("severity") or "none"), 99) > _SEVERITY_RANK.get(
        str(settings["diagnosis_active_max_diff_severity"]),
        _SEVERITY_RANK["warning"],
    ):
        blockers.append("diff_severity_too_high")
    if int((_to_dict(diff.get("counters")).get("critical_count") or diff.get("critical_count") or 0) or 0) > 0:
        blockers.append("critical_diff_present")
    if not set(shadow_tools).issubset(set(legacy_runtime_tools)):
        blockers.append("tool_scope_violation")
    if _removes_safety_node(legacy_nodes, shadow_nodes):
        blockers.append("safety_node_removed")
    if "workorder_decision" in shadow_nodes or final_nodes.intersection({"workorder_decision"}):
        blockers.append("workorder_decision_not_allowed")
    if _unsafe_output_semantics(shadow):
        blockers.append("unsafe_action_completion_semantics")
    if not final_nodes.intersection(_DIAGNOSIS_ACTIVE_NODES):
        blockers.append("empty_diagnosis_active_scope")
    return _dedupe(blockers)


def _goal_blockers(goal_set: dict[str, Any], goals_value: Any, *, task_family: str) -> list[str]:
    blockers: list[str] = []
    goals = goal_set.get("goals") if isinstance(goal_set, dict) else goals_value
    for goal in goals or []:
        data = _to_dict(goal)
        goal_type = str(data.get("goal_type") or "")
        risk = str(data.get("risk_level") or "")
        if goal_type in _BLOCKED_GOAL_TYPES:
            if goal_type == "decide_workorder":
                blockers.append("action_or_workorder_not_migrated")
            elif task_family != "diagnosis":
                blockers.append("diagnosis_not_migrated")
        if risk in _BLOCKED_RISK_LEVELS:
            blockers.append(f"risk_not_migrated:{risk}")
    return blockers


def _final_enabled_nodes(
    task_family: str,
    legacy_nodes: dict[str, bool],
    shadow_nodes: set[str],
    *,
    decision: Any,
) -> set[str]:
    if task_family == "diagnosis":
        allowed_nodes = set(_DIAGNOSIS_ACTIVE_NODES)
        if not _report_explicitly_requested(decision):
            allowed_nodes.discard("report")
        projected = {node for node in shadow_nodes if node in allowed_nodes and legacy_nodes.get(node)}
        preserved_safety = {node for node in _SAFETY_NODES if legacy_nodes.get(node)}
        return projected | preserved_safety
    allowed_nodes = _READ_ONLY_NODE_BY_FAMILY.get(task_family, set()) | _SAFETY_NODES
    return {node for node in shadow_nodes if node in allowed_nodes and legacy_nodes.get(node)}


def _tools_for_nodes(nodes: set[str]) -> set[str]:
    tools: set[str] = set()
    for node in nodes:
        tools.update(_READ_ONLY_TOOLS_BY_NODE.get(node, set()))
    return tools


def _report_explicitly_requested(decision: Any) -> bool:
    if str(getattr(decision, "requested_output", "") or "") == "report":
        return True
    goals = _goals_for_decision(decision)
    return any(str(goal.get("goal_type") or "") == "generate_report" for goal in goals)


def _needs_runtime_for_active(decision: Any, readiness: dict[str, Any], shadow: dict[str, Any]) -> bool:
    if _pure_knowledge_explanation(decision, shadow):
        return False
    return str(readiness.get("diagnosis_mode") or "") in {"alarm_triage", "fault_diagnosis"}


def _pure_knowledge_explanation(decision: Any, shadow: dict[str, Any]) -> bool:
    intents = set(legacy_intents(decision))
    shadow_nodes = _shadow_enabled_nodes(shadow)
    return bool("explain_alarm_code" in intents and "check_current_status" not in intents and "sql" not in shadow_nodes)


def _needs_manual_for_active(decision: Any, readiness: dict[str, Any]) -> bool:
    if readiness.get("has_alarm_or_fault_context"):
        return True
    intents = set(legacy_intents(decision))
    return bool(intents.intersection({"explain_alarm_code", "resolution_recommendation"}))


def _goal_has_action_or_high_risk(decision: Any) -> bool:
    for goal in _goals_for_decision(decision):
        goal_type = str(goal.get("goal_type") or "")
        risk = str(goal.get("risk_level") or "")
        if goal_type == "decide_workorder" or risk in _BLOCKED_RISK_LEVELS:
            return True
    return False


def _goals_for_decision(decision: Any) -> list[dict[str, Any]]:
    goal_set = getattr(decision, "goal_set", {}) or {}
    goals = goal_set.get("goals") if isinstance(goal_set, dict) else None
    if not goals:
        goals = getattr(decision, "goals", []) or []
    return [_to_dict(goal) for goal in goals or []]


def _unsafe_output_semantics(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_unsafe_output_semantics(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_unsafe_output_semantics(item) for item in value)
    text = str(value or "")
    if not text:
        return False
    return any(word in text for word in _DANGEROUS_OUTPUT_WORDS)


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
