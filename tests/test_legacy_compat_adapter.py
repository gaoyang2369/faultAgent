from __future__ import annotations

from fault_diagnosis.single_agent.compat import (
    build_legacy_intent_stack,
    build_task_payload_for_compat,
    ensure_legacy_intent,
    explain_legacy_field_usage,
    goal_labels_for_summary,
    has_legacy_intent,
    mark_goal_projection_mismatch,
    route_is_action_or_workorder,
    route_is_action_request,
    project_task_type_for_compat,
    sync_goal_projection_for_legacy_route,
)
from fault_diagnosis.single_agent.workflow import GoalSet, IntentGoal, TaskRoute, TaskType


def _goal_set() -> GoalSet:
    return GoalSet(
        primary_goal_id="goal_1_check_runtime_status",
        goals=[
            IntentGoal(
                goal_id="goal_1_check_runtime_status",
                goal_type="check_runtime_status",
                description="查询当前运行状态",
            )
        ],
        execution_order=["goal_1_check_runtime_status"],
        intent_stack_projection=["check_current_status", "fault_diagnosis"],
        goal_summary="goals: check_runtime_status",
    )


def test_build_legacy_intent_stack_stable_merges_projection_then_legacy_candidates() -> None:
    merged = build_legacy_intent_stack(
        _goal_set(),
        ["fault_diagnosis", "resolution_recommendation", "", "check_current_status"],
    )

    assert merged == ["check_current_status", "fault_diagnosis", "resolution_recommendation"]


def test_mark_goal_projection_mismatch_preserves_goal_set_shape_and_summary() -> None:
    marked = mark_goal_projection_mismatch(_goal_set(), ["resolution_recommendation"])

    assert isinstance(marked, GoalSet)
    assert marked.intent_stack_projection == ["check_current_status", "fault_diagnosis"]
    assert "projection differs from legacy intents" in marked.goal_summary
    assert "projected=[check_current_status, fault_diagnosis]" in marked.goal_summary
    assert "legacy=[resolution_recommendation]" in marked.goal_summary


def test_sync_goal_projection_for_legacy_route_adds_route_adjustment_intents() -> None:
    route = TaskRoute(
        primary_task_type=TaskType.FAULT_DIAGNOSIS,
        intent_stack=["fault_diagnosis", "workorder_decision"],
        goal_set={
            "primary_goal_id": "goal_1_diagnose_fault",
            "intent_stack_projection": ["fault_diagnosis"],
            "goal_summary": "goals: diagnose_fault",
        },
    )

    sync_goal_projection_for_legacy_route(route)

    assert route.goal_set["intent_stack_projection"] == ["fault_diagnosis", "workorder_decision"]
    assert "projection synchronized with legacy route adjustments" in route.goal_summary


def test_project_task_type_for_compat_normalizes_enum_values() -> None:
    route = TaskRoute(
        primary_task_type=TaskType.ALARM_TRIAGE,
        candidate_task_types=[TaskType.ALARM_TRIAGE, TaskType.KNOWLEDGE_QA, TaskType.ALARM_TRIAGE],
    )

    projection = project_task_type_for_compat(route)

    assert projection == {
        "primary_task_type": "alarm_triage",
        "candidate_task_types": ["alarm_triage", "knowledge_qa"],
    }


def test_explain_legacy_field_usage_marks_fields_deprecated_but_retained() -> None:
    usage = explain_legacy_field_usage()

    assert usage["status"] == "deprecated_compatibility_fields"
    assert usage["remove_now"] is False
    assert usage["fields"]["TaskType"]["deprecated_for"] == "new internal planning logic"
    assert usage["fields"]["intent_stack"]["source"] == "GoalSet projection plus legacy candidates merge"


def test_compat_helpers_prefer_goal_set_but_keep_legacy_output_shape() -> None:
    route = TaskRoute(
        primary_task_type=TaskType.FAULT_DIAGNOSIS,
        intent_stack=[],
        task_family="diagnosis",
        action_target="workorder",
        goal_set={
            "intent_stack_projection": ["workorder_decision"],
            "goals": [{"goal_id": "goal_1_decide_workorder", "goal_type": "decide_workorder"}],
        },
    )

    assert has_legacy_intent(route, "workorder_decision")
    assert route_is_action_or_workorder(route) is True
    assert route_is_action_request(route) is False
    assert goal_labels_for_summary(route) == ["工单判断保护"]
    assert build_task_payload_for_compat(route, include_task_type_alias=True) == {
        "task_type": "fault_diagnosis",
        "primary_task_type": "fault_diagnosis",
        "candidate_task_types": [],
        "intent_stack": ["workorder_decision"],
    }


def test_ensure_legacy_intent_keeps_existing_order_and_dedupes() -> None:
    route = TaskRoute(
        primary_task_type=TaskType.FAULT_DIAGNOSIS,
        intent_stack=["fault_diagnosis"],
    )

    ensure_legacy_intent(route, "workorder_decision")
    ensure_legacy_intent(route, "fault_diagnosis")

    assert route.intent_stack == ["fault_diagnosis", "workorder_decision"]
