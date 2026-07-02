from __future__ import annotations

from fault_diagnosis.single_agent.compat import (
    build_legacy_intent_stack,
    explain_legacy_field_usage,
    project_task_type_for_compat,
)
from fault_diagnosis.single_agent.workflow import GoalSet, IntentGoal, TaskRoute


def _goal_set(*goal_types: str, projection: list[str] | None = None) -> GoalSet:
    goals = [
        IntentGoal(goal_id=f"goal_{index}_{goal_type}", goal_type=goal_type, description=goal_type)
        for index, goal_type in enumerate(goal_types, start=1)
    ]
    return GoalSet(
        primary_goal_id=goals[0].goal_id if goals else None,
        goals=goals,
        execution_order=[goal.goal_id for goal in goals],
        legacy_intent_projection=projection or [],
        goal_summary="goal-native test",
    )


def test_legacy_intent_stack_is_one_way_projection_from_goal_set() -> None:
    goal_set = _goal_set("check_runtime_status", "recommend_resolution", projection=["check_current_status"])

    merged = build_legacy_intent_stack(goal_set, ["resolution_recommendation", "check_current_status"])

    assert merged == ["check_current_status", "resolution_recommendation"]


def test_project_task_type_for_compat_uses_goal_axes() -> None:
    route = TaskRoute(task_family="reporting", requested_output="report", goal_set=_goal_set("generate_report").model_dump())

    assert project_task_type_for_compat(route) == "report_generation"


def test_explain_legacy_field_usage_documents_projection_boundary() -> None:
    usage = explain_legacy_field_usage()

    assert usage["status"] == "compatibility_projection_only"
    assert usage["reverse_sync_allowed"] is False
    assert usage["internal_execution_inputs"] == [
        "ResolvedContext",
        "GoalSet",
        "task_family",
        "policy_id",
        "readiness",
        "manual_confirmation",
    ]
