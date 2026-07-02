"""Goal-native classification helpers shared by workflow internals."""

from __future__ import annotations

from typing import Any


def goal_types(route_or_decision: Any) -> list[str]:
    """Return goal types from the current GoalSet payload."""

    data = _model_dump(route_or_decision)
    raw_goal_set = data.get("goal_set") if data else _field(route_or_decision, "goal_set")
    goal_set = _model_dump(raw_goal_set or route_or_decision)
    goals = data.get("goals") or goal_set.get("goals") or _field(route_or_decision, "goals", []) or []
    return _dedupe(
        [
            str(_model_dump(goal).get("goal_type") or "").strip()
            for goal in goals
            if str(_model_dump(goal).get("goal_type") or "").strip()
        ]
    )


def goal_labels_for_summary(route_or_decision: Any) -> list[str]:
    labels = {
        "explain_fault_code": "解释故障码",
        "check_runtime_status": "核查当前状态",
        "refresh_current_status": "核查当前状态",
        "diagnose_fault": "故障诊断",
        "assess_severity": "评估严重程度",
        "recommend_resolution": "给出处置建议",
        "generate_report": "生成报告",
        "decide_workorder": "工单判断保护",
        "create_workorder_draft": "工单草稿保护",
        "dispatch_workorder": "派单保护",
        "answer_meta_question": "权限范围说明",
        "clarify_missing_context": "澄清上下文",
    }
    return _dedupe([labels.get(value, value) for value in goal_types(route_or_decision)])


def requests_action_or_workorder(route_or_decision: Any) -> bool:
    goals = set(goal_types(route_or_decision))
    if goals.intersection({"decide_workorder", "create_workorder_draft", "dispatch_workorder"}):
        return True
    if str(_field(route_or_decision, "task_family") or "") == "action_or_workorder":
        return True
    if str(_field(route_or_decision, "action_target") or ""):
        return True
    if str(_field(route_or_decision, "action_type") or ""):
        return True
    return False


def requests_report(route_or_decision: Any) -> bool:
    return "generate_report" in set(goal_types(route_or_decision)) or str(
        _field(route_or_decision, "requested_output") or ""
    ) == "report"


def requests_runtime_status(route_or_decision: Any) -> bool:
    return bool(set(goal_types(route_or_decision)).intersection({"check_runtime_status", "refresh_current_status"}))


def task_profile_for_compat(route_or_decision: Any) -> str:
    """Return the legacy task label only for output-schema compatibility."""

    goals = set(goal_types(route_or_decision))
    task_family = str(_field(route_or_decision, "task_family") or "")
    requested_output = str(_field(route_or_decision, "requested_output") or "")
    if requested_output == "report" or "generate_report" in goals or task_family == "reporting":
        return "report_generation"
    if task_family == "action_or_workorder" or requests_action_or_workorder(route_or_decision):
        return "action_request"
    if task_family == "meta" or "answer_meta_question" in goals:
        return "permission_scope_query"
    if task_family == "knowledge_lookup":
        return "knowledge_qa"
    if task_family == "runtime_status":
        return "status_query"
    if "explain_fault_code" in goals and goals.intersection({"check_runtime_status", "refresh_current_status", "recommend_resolution"}):
        return "alarm_triage"
    if "diagnose_fault" in goals:
        return "fault_diagnosis"
    if "assess_severity" in goals:
        return "health_assessment"
    if "explain_fault_code" in goals:
        return "knowledge_qa"
    return "fault_diagnosis"


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return dict(value or {}) if isinstance(value, dict) else {}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
