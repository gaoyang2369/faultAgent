"""Goal-native task-family resolution."""

from __future__ import annotations

from typing import Any

from .contracts import IntentGoal, TaskFamily, TaskFamilyResolution

PUBLIC_TASK_FAMILIES: tuple[TaskFamily, ...] = (
    "knowledge_lookup",
    "runtime_status",
    "diagnosis",
    "reporting",
    "action_or_workorder",
    "meta",
)


def resolve_task_family(
    *,
    requested_output: str | None = None,
    goals: list[IntentGoal] | list[dict[str, Any]] | None = None,
    resolved_context: dict[str, Any] | None = None,
    action_target: str | None = None,
    action_type: str | None = None,
    **_: Any,
) -> TaskFamilyResolution:
    """Return the coarse task family from GoalSet and context axes."""

    family = (
        _action_hint(action_target, action_type)
        or _strong_output_hint(requested_output)
        or _primary_goal_hint(goals)
        or _context_hint(resolved_context)
        or "diagnosis"
    )
    return TaskFamilyResolution(
        task_family=family,
        reason=f"resolved from goal/output/context axes:{family}",
        source="goal_hint_fallback",
        warnings=[],
    )


def _action_hint(action_target: str | None, action_type: str | None) -> TaskFamily | None:
    if str(action_target or "").strip() or str(action_type or "").strip():
        return "action_or_workorder"
    return None


def _strong_output_hint(requested_output: str | None) -> TaskFamily | None:
    value = str(requested_output or "").strip().lower()
    if value == "report":
        return "reporting"
    if value in {"action_confirmation", "workorder_decision"}:
        return "action_or_workorder"
    if value in {"permission_scope", "clarification"}:
        return "meta"
    return None


def _primary_goal_hint(goals: list[IntentGoal] | list[dict[str, Any]] | None) -> TaskFamily | None:
    goal_types = [_goal_type(item) for item in goals or []]
    if not goal_types:
        return None
    if "generate_report" in goal_types:
        return "reporting"
    if any(item in goal_types for item in ("decide_workorder", "create_workorder_draft", "dispatch_workorder")):
        return "action_or_workorder"
    if "answer_meta_question" in goal_types or "clarify_missing_context" in goal_types:
        return "meta"
    if goal_types == ["check_runtime_status"] or goal_types == ["refresh_current_status"]:
        return "runtime_status"
    if set(goal_types).issubset({"explain_fault_code", "recommend_resolution"}):
        return "knowledge_lookup"
    return "diagnosis"


def _context_hint(resolved_context: dict[str, Any] | None) -> TaskFamily | None:
    relation = str((resolved_context or {}).get("relation_to_previous") or "").strip()
    if relation == "report_handoff":
        return "reporting"
    if relation == "action_followup":
        return "action_or_workorder"
    return None


def _goal_type(item: IntentGoal | dict[str, Any]) -> str:
    if isinstance(item, IntentGoal):
        return item.goal_type
    if isinstance(item, dict):
        return str(item.get("goal_type") or "")
    return ""
