"""Deprecated legacy-intent compatibility projections.

These helpers keep the old ``TaskType`` / ``intent_stack`` surface stable while
new planning work moves toward GoalSet and planner-gated projections.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


LEGACY_FIELD_USAGE_SCHEMA_VERSION = "legacy_field_usage.v1"


def build_legacy_intent_stack(goal_set: Any, legacy_candidates: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return the deprecated compatibility intent stack.

    The compatibility contract is intentionally unchanged:
    ``stable_dedupe(goal_set.intent_stack_projection + legacy_candidates)``.
    """

    return _dedupe([*_intent_stack_projection(goal_set), *_strings(legacy_candidates)])


def mark_goal_projection_mismatch(goal_set: Any, legacy_candidates: list[str] | tuple[str, ...] | None) -> Any:
    """Append the existing mismatch note to a GoalSet without changing its shape."""

    legacy_intents = _strings(legacy_candidates)
    projected = _intent_stack_projection(goal_set)
    data = _model_dump(goal_set)
    projected_text = ", ".join(projected) or "none"
    legacy_text = ", ".join(legacy_intents) or "none"
    suffix = f"projection differs from legacy intents: projected=[{projected_text}], legacy=[{legacy_text}]"
    summary = str(data.get("goal_summary") or "").strip()
    data["goal_summary"] = f"{summary}；{suffix}" if summary else suffix
    return _model_validate_like(goal_set, data)


def sync_goal_projection_for_legacy_route(route: Any) -> None:
    """Synchronize GoalSet debug projection after legacy route adjustments.

    Evidence-gap and context follow-up handling may append legacy intents after
    initial routing. This preserves the previous debug payload behavior.
    """

    goal_set = dict(getattr(route, "goal_set", {}) or {})
    if not goal_set:
        return
    projection = _strings(goal_set.get("intent_stack_projection") or [])
    changed = False
    for intent in _strings(getattr(route, "intent_stack", []) or []):
        if intent not in projection:
            projection.append(intent)
            changed = True
    if not changed:
        return
    goal_set["intent_stack_projection"] = projection
    summary = str(goal_set.get("goal_summary") or "").strip()
    suffix = "projection synchronized with legacy route adjustments"
    goal_set["goal_summary"] = f"{summary}；{suffix}" if summary else suffix
    route.goal_set = goal_set
    route.goal_summary = goal_set["goal_summary"]


def project_task_type_for_compat(route_or_decision: Any) -> dict[str, Any]:
    """Return deprecated task-type fields for compatibility consumers."""

    primary = _enum_value(getattr(route_or_decision, "primary_task_type", None))
    candidates = [_enum_value(item) for item in list(getattr(route_or_decision, "candidate_task_types", []) or [])]
    return {
        "primary_task_type": primary or "fault_diagnosis",
        "candidate_task_types": _dedupe(candidates),
    }


def project_route_fields_for_compat(route_or_decision: Any) -> dict[str, Any]:
    """Return deprecated route fields for public compatibility outputs."""

    return {
        **project_task_type_for_compat(route_or_decision),
        "intent_stack": legacy_intents(route_or_decision),
    }


def legacy_planning_input_fields_for_compat(source: Any | None = None, legacy_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return deprecated PlanningInput fields from a route, decision, or kwargs."""

    legacy_kwargs = dict(legacy_kwargs or {})
    if isinstance(source, dict):
        source = _DictObject(source)
    if source is None:
        source = _DictObject(legacy_kwargs)
    route_fields = project_route_fields_for_compat(source)
    primary = legacy_kwargs.get("primary_task_type", route_fields["primary_task_type"])
    intents = legacy_kwargs.get("intent_stack", route_fields["intent_stack"])
    return {
        "primary_task_type": _enum_value(primary) or "fault_diagnosis",
        "intent_stack": _dedupe(_strings(intents)),
    }


def legacy_projection_for_shadow_plan(planning_input: Any, legacy: dict[str, Any]) -> dict[str, Any]:
    """Return the shadow-plan legacy projection without leaking field reads."""

    return {
        "legacy_primary_task_type": legacy_task_value(planning_input, default=""),
        "legacy_intent_stack": legacy_intents(planning_input),
        "legacy_enabled_nodes": legacy.get("legacy_enabled_nodes") or legacy.get("enabled_nodes"),
        "legacy_runtime_tools": legacy.get("legacy_runtime_tools") or legacy.get("runtime_tools"),
        "legacy_requested_output": legacy.get("legacy_requested_output") or legacy.get("requested_output"),
        "legacy_evidence_mode": legacy.get("legacy_evidence_mode") or legacy.get("evidence_mode"),
        "legacy_should_refresh_runtime_data": bool(
            legacy.get("legacy_should_refresh_runtime_data", legacy.get("should_refresh_runtime_data", False))
        ),
    }


def legacy_projection_warnings_for_planning_input(planning_input: Any) -> list[str]:
    """Return compatibility warnings for missing deprecated planning fields."""

    warnings: list[str] = []
    if not legacy_task_value(planning_input, default=""):
        warnings.append("missing_legacy_primary_task_type")
    if not legacy_intents(planning_input):
        warnings.append("missing_legacy_intent_stack")
    return warnings


def merge_legacy_projection_for_planning_diff(legacy: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    """Merge shadow legacy_projection into diff legacy view."""

    merged = dict(legacy)
    mapping = {
        "primary_task_type": "legacy_primary_task_type",
        "intent_stack": "legacy_intent_stack",
        "enabled_nodes": "legacy_enabled_nodes",
        "runtime_tools": "legacy_runtime_tools",
        "requested_output": "legacy_requested_output",
        "evidence_mode": "legacy_evidence_mode",
        "should_refresh_runtime_data": "legacy_should_refresh_runtime_data",
    }
    for key, projection_key in mapping.items():
        if _missing_compat(merged.get(key)) and projection_key in projection:
            merged[key] = projection.get(projection_key)
    return merged


def planner_gate_task_fields_for_compat(route_or_decision: Any) -> dict[str, Any]:
    """Return deprecated task fields accepted by PlannerGateDecision."""

    return {"primary_task_type": legacy_task_value(route_or_decision, default="")}


def build_task_payload_for_compat(route_or_decision: Any, *, include_task_type_alias: bool = False) -> dict[str, Any]:
    """Return legacy task metadata for evidence/artifact compatibility."""

    projection = project_route_fields_for_compat(route_or_decision)
    if include_task_type_alias:
        projection = {"task_type": legacy_task_value(route_or_decision), **projection}
    return projection


def project_plan_axis_fields_for_compat(route_or_decision: Any) -> dict[str, Any]:
    """Return legacy fields used only in plan/debug axes."""

    task_projection = project_task_type_for_compat(route_or_decision)
    return {
        "domain_task": task_projection["primary_task_type"],
        "candidate_task_types": task_projection["candidate_task_types"],
        "intent_stack": legacy_intents(route_or_decision),
    }


def legacy_task_value(route_or_decision: Any, *, default: str = "fault_diagnosis") -> str:
    """Return the deprecated primary task value for compatibility fallbacks."""

    primary = _enum_value(getattr(route_or_decision, "primary_task_type", None))
    return primary or default


def is_legacy_task(route_or_decision: Any, *task_values: str) -> bool:
    """Return whether the compatibility task value matches any supplied value."""

    return legacy_task_value(route_or_decision) in set(task_values)


def legacy_intents(route_or_decision: Any) -> list[str]:
    """Return deprecated intent projection with GoalSet preferred for new logic."""

    goal_projection = _intent_stack_projection(getattr(route_or_decision, "goal_set", {}) or {})
    raw_intents = _strings(getattr(route_or_decision, "intent_stack", []) or [])
    return _dedupe([*goal_projection, *raw_intents])


def has_legacy_intent(route_or_decision: Any, intent: str) -> bool:
    """Return whether the compatibility intent projection contains ``intent``."""

    return intent in set(legacy_intents(route_or_decision))


def ensure_legacy_intent(route_or_decision: Any, intent: str) -> None:
    """Append a deprecated compatibility intent if the object stores one."""

    value = str(intent or "").strip()
    if not value:
        return
    current = _strings(getattr(route_or_decision, "intent_stack", []) or [])
    if value not in current:
        current.append(value)
        route_or_decision.intent_stack = current


def goal_types(route_or_decision: Any) -> list[str]:
    """Return GoalSet goal types without requiring legacy intent fields."""

    goals = _goal_dicts(getattr(route_or_decision, "goals", []) or [])
    goal_set = getattr(route_or_decision, "goal_set", {}) or {}
    if not goals:
        goals = _goal_dicts(_model_dump(goal_set).get("goals") or [])
    return _dedupe([str(goal.get("goal_type") or "").strip() for goal in goals if str(goal.get("goal_type") or "").strip()])


def route_requests_workorder_followup(route_or_decision: Any) -> bool:
    """Return whether the request asks for workorder handling.

    GoalSet and action target are preferred. Deprecated intents remain only as a
    compatibility fallback until workflow policy migration is complete.
    """

    goals = set(goal_types(route_or_decision))
    if goals.intersection({"decide_workorder", "create_workorder_draft", "dispatch_workorder"}):
        return True
    if str(getattr(route_or_decision, "action_target", "") or "") == "workorder":
        return True
    return bool({"workorder_decision", "create_workorder_draft", "dispatch_workorder"}.intersection(legacy_intents(route_or_decision)))


def route_is_action_or_workorder(route_or_decision: Any) -> bool:
    """Return high-risk action/workorder classification with new fields first."""

    if str(getattr(route_or_decision, "task_family", "") or "") == "action_or_workorder":
        return True
    if str(getattr(route_or_decision, "action_type", "") or ""):
        return True
    if route_requests_workorder_followup(route_or_decision):
        return True
    return is_legacy_task(route_or_decision, "action_request")


def route_is_action_request(route_or_decision: Any) -> bool:
    """Return whether the deprecated primary task is an action request."""

    return is_legacy_task(route_or_decision, "action_request")


def goal_labels_for_summary(route_or_decision: Any) -> list[str]:
    """Return compact goal labels for user-facing summary prefixes."""

    labels = {
        "explain_fault_code": "解释故障码",
        "check_runtime_status": "核查当前状态",
        "refresh_current_status": "核查当前状态",
        "diagnose_fault": "故障诊断",
        "assess_severity": "评估严重程度",
        "recommend_resolution": "给出处置建议",
        "generate_report": "生成报告",
        "decide_workorder": "工单判断保护",
        "answer_meta_question": "权限范围说明",
        "explain_alarm_code": "解释故障码",
        "check_current_status": "核查当前状态",
        "fault_impact": "评估影响范围",
        "severity_assessment": "评估严重程度",
        "resolution_recommendation": "给出处置建议",
        "report_generation": "生成报告",
        "action_request": "动作请求保护",
        "fault_diagnosis": "故障诊断",
    }
    values = goal_types(route_or_decision)
    if not values:
        values = legacy_intents(route_or_decision)
    return _dedupe([labels.get(value, value) for value in values if value])


def explain_legacy_field_usage() -> dict[str, Any]:
    """Return a machine-readable deprecation note for audits and docs."""

    return {
        "schema_version": LEGACY_FIELD_USAGE_SCHEMA_VERSION,
        "status": "deprecated_compatibility_fields",
        "fields": {
            "TaskType": {
                "role": "legacy primary workflow classifier",
                "deprecated_for": "new internal planning logic",
                "retained_for": ["workflow_policy", "frontend", "eval", "artifact", "trace", "sse"],
            },
            "primary_task_type": {
                "role": "serialized TaskType compatibility projection",
                "deprecated_for": "new internal planning logic",
                "retained_for": ["workflow_policy", "frontend", "eval", "artifact", "trace", "sse"],
            },
            "candidate_task_types": {
                "role": "legacy alternate task-type compatibility projection",
                "deprecated_for": "new internal planning logic",
                "retained_for": ["frontend", "eval", "artifact", "trace", "sse"],
            },
            "intent_stack": {
                "role": "legacy policy intent projection",
                "source": "GoalSet projection plus legacy candidates merge",
                "deprecated_for": "new internal planning logic",
                "retained_for": ["workflow_policy", "evidence_gap", "frontend", "eval", "artifact", "trace", "sse"],
            },
        },
        "new_internal_path": [
            "ResolvedContext",
            "GoalSet",
            "TaskFamily",
            "ShadowPlanner",
            "PlanningDiff",
            "PlannerGate",
        ],
        "remove_now": False,
    }


def _intent_stack_projection(goal_set: Any) -> list[str]:
    if goal_set is None:
        return []
    if isinstance(goal_set, dict):
        return _strings(goal_set.get("intent_stack_projection") or [])
    return _strings(getattr(goal_set, "intent_stack_projection", []) or [])


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return dict(value or {}) if isinstance(value, dict) else {}


def _model_validate_like(original: Any, data: dict[str, Any]) -> Any:
    model_type = type(original)
    if hasattr(model_type, "model_validate"):
        return model_type.model_validate(data)
    return data


def _missing_compat(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


class _DictObject:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _goal_dicts(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    for item in values:
        data = _model_dump(item)
        if data:
            result.append(data)
    return result


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "").strip()


def _strings(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
