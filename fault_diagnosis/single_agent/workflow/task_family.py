"""Task-family compatibility mapping for observational outputs."""

from __future__ import annotations

from typing import Any

from .contracts import IntentGoal, TaskFamily, TaskFamilyResolution, TaskType

PUBLIC_TASK_FAMILIES: tuple[TaskFamily, ...] = (
    "knowledge_lookup",
    "runtime_status",
    "diagnosis",
    "reporting",
    "action_or_workorder",
    "meta",
)

_TASK_TYPE_MAPPING: dict[str, TaskFamily] = {
    TaskType.KNOWLEDGE_QA.value: "knowledge_lookup",
    TaskType.STATUS_QUERY.value: "runtime_status",
    TaskType.ALARM_TRIAGE.value: "diagnosis",
    TaskType.FAULT_DIAGNOSIS.value: "diagnosis",
    TaskType.ROOT_CAUSE_ANALYSIS.value: "diagnosis",
    TaskType.HEALTH_ASSESSMENT.value: "diagnosis",
    TaskType.REPORT_GENERATION.value: "reporting",
    TaskType.ACTION_REQUEST.value: "action_or_workorder",
    TaskType.PERMISSION_SCOPE_QUERY.value: "meta",
    "direct_response": "meta",
    "greeting": "meta",
    "thanks": "meta",
    "capability": "meta",
}


def resolve_task_family(
    *,
    task_type: TaskType | str,
    requested_output: str | None = None,
    goals: list[IntentGoal] | list[dict[str, Any]] | None = None,
    resolved_context: dict[str, Any] | None = None,
    intent_stack: list[str] | None = None,
) -> TaskFamilyResolution:
    """Return the coarse task family without influencing execution policy."""

    task_type_value = _task_type_value(task_type)
    warnings: list[str] = []
    if task_type_value in _TASK_TYPE_MAPPING:
        family = _TASK_TYPE_MAPPING[task_type_value]
        mismatch = _strong_mismatch_hint(
            family,
            requested_output=requested_output,
            goals=goals,
            intent_stack=intent_stack,
        )
        if mismatch:
            warnings.append("task_family_goal_mismatch")
        reason = f"mapped from task_type:{task_type_value}"
        if mismatch:
            reason = f"{reason}; strong hint suggested {mismatch}"
        return TaskFamilyResolution(
            task_family=family,
            reason=reason,
            source="direct_response" if task_type_value in {"direct_response", "greeting", "thanks", "capability"} else "task_type_mapping",
            warnings=warnings,
        )

    warnings.append("unknown_task_type")
    fallback = _fallback_family(
        requested_output=requested_output,
        goals=goals,
        resolved_context=resolved_context,
        intent_stack=intent_stack,
    )
    return TaskFamilyResolution(
        task_family=fallback,
        reason=f"unknown task_type:{task_type_value}; fallback to {fallback}",
        source="unknown_fallback",
        warnings=warnings,
    )


def _task_type_value(task_type: TaskType | str) -> str:
    if isinstance(task_type, TaskType):
        return task_type.value
    return str(task_type or "").strip().lower() or "unknown"


def _strong_mismatch_hint(
    mapped_family: TaskFamily,
    *,
    requested_output: str | None,
    goals: list[IntentGoal] | list[dict[str, Any]] | None,
    intent_stack: list[str] | None,
) -> TaskFamily | None:
    hint = _strong_output_hint(requested_output)
    if hint and hint != mapped_family:
        return hint
    goal_hint = _primary_goal_hint(goals)
    if goal_hint and goal_hint != mapped_family:
        return goal_hint
    intent_hint = _strong_intent_hint(intent_stack)
    if intent_hint and intent_hint != mapped_family:
        return intent_hint
    return None


def _fallback_family(
    *,
    requested_output: str | None,
    goals: list[IntentGoal] | list[dict[str, Any]] | None,
    resolved_context: dict[str, Any] | None,
    intent_stack: list[str] | None,
) -> TaskFamily:
    return (
        _strong_output_hint(requested_output)
        or _primary_goal_hint(goals)
        or _strong_intent_hint(intent_stack)
        or _context_hint(resolved_context)
        or "meta"
    )


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
    if "decide_workorder" in goal_types:
        return "action_or_workorder"
    if "answer_meta_question" in goal_types or "clarify_missing_context" in goal_types:
        return "meta"
    return None


def _strong_intent_hint(intent_stack: list[str] | None) -> TaskFamily | None:
    intents = {str(item or "").strip() for item in intent_stack or [] if str(item or "").strip()}
    if "report_generation" in intents:
        return "reporting"
    if intents.intersection({"action_request", "dispatch_workorder", "create_workorder_draft"}):
        return "action_or_workorder"
    if "permission_scope_query" in intents:
        return "meta"
    return None


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
