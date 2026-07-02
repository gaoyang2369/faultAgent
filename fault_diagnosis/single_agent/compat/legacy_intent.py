"""One-way legacy field projections for public compatibility outputs."""

from __future__ import annotations

from typing import Any


LEGACY_FIELD_USAGE_SCHEMA_VERSION = "legacy_field_usage.v2"

_TASK_BY_GOAL = {
    "answer_meta_question": "permission_scope_query",
    "generate_report": "report_generation",
    "decide_workorder": "action_request",
    "create_workorder_draft": "action_request",
    "dispatch_workorder": "action_request",
    "check_runtime_status": "status_query",
    "refresh_current_status": "status_query",
    "explain_fault_code": "knowledge_qa",
    "diagnose_fault": "fault_diagnosis",
    "assess_severity": "health_assessment",
    "recommend_resolution": "fault_diagnosis",
    "clarify_missing_context": "status_query",
}

_INTENT_BY_GOAL = {
    "answer_meta_question": "permission_scope_query",
    "generate_report": "report_generation",
    "decide_workorder": "workorder_decision",
    "create_workorder_draft": "create_workorder_draft",
    "dispatch_workorder": "dispatch_workorder",
    "check_runtime_status": "check_current_status",
    "refresh_current_status": "check_current_status",
    "explain_fault_code": "explain_alarm_code",
    "diagnose_fault": "fault_diagnosis",
    "assess_severity": "severity_assessment",
    "recommend_resolution": "resolution_recommendation",
    "clarify_missing_context": "clarify_missing_context",
}


def project_task_type_for_compat(route_or_goal_set: Any) -> str:
    """Project the deprecated serialized task type from goal-native fields."""

    data = _model_dump(route_or_goal_set)
    goal_set = _model_dump(data.get("goal_set") or route_or_goal_set)
    goal_types = _goal_types(goal_set or data)
    requested_output = str(data.get("requested_output") or "").strip()
    task_family = str(data.get("task_family") or "").strip()
    action_target = str(data.get("action_target") or "").strip()
    if requested_output == "report" or "generate_report" in goal_types or task_family == "reporting":
        return "report_generation"
    if task_family == "action_or_workorder" or action_target:
        return "action_request"
    if task_family == "meta" or "answer_meta_question" in goal_types:
        return "permission_scope_query"
    if goal_types == ["check_runtime_status"] or task_family == "runtime_status":
        return "status_query"
    if task_family == "knowledge_lookup":
        return "knowledge_qa"
    if "diagnose_fault" in goal_types:
        return "fault_diagnosis"
    if "assess_severity" in goal_types:
        return "health_assessment"
    for goal_type in goal_types:
        if goal_type in _TASK_BY_GOAL:
            return _TASK_BY_GOAL[goal_type]
    return "fault_diagnosis"


def build_legacy_intent_stack(goal_set: Any, legacy_candidates: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Project the deprecated public intent list from GoalSet only."""

    data = _model_dump(goal_set)
    projected = _strings(data.get("legacy_intent_projection") or data.get("intent_stack_projection") or [])
    if not projected:
        projected = [_INTENT_BY_GOAL.get(item, item) for item in _goal_types(data)]
    return _dedupe([*projected, *_strings(legacy_candidates)])


def explain_legacy_field_usage() -> dict[str, Any]:
    """Return the current compatibility boundary for audits and docs."""

    return {
        "schema_version": LEGACY_FIELD_USAGE_SCHEMA_VERSION,
        "status": "compatibility_projection_only",
        "retained_fields": ["primary_task_type", "candidate_task_types", "intent_stack"],
        "retained_for": ["SSE complete payloads", "artifact history", "frontend compatibility", "dev fixtures"],
        "internal_execution_inputs": [
            "ResolvedContext",
            "GoalSet",
            "task_family",
            "policy_id",
            "readiness",
            "manual_confirmation",
        ],
        "reverse_sync_allowed": False,
    }


def _goal_types(value: dict[str, Any]) -> list[str]:
    explicit = value.get("goal_types") or []
    if explicit:
        return _dedupe(_strings(explicit))
    goals = value.get("goals") or []
    return _dedupe(
        [
            str(_model_dump(goal).get("goal_type") or "").strip()
            for goal in goals
            if str(_model_dump(goal).get("goal_type") or "").strip()
        ]
    )


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return dict(value or {}) if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return [str(value).strip()] if str(value or "").strip() else []


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
