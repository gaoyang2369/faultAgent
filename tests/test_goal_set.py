from __future__ import annotations

from fault_diagnosis.context import ResolvedContext
from fault_diagnosis.single_agent.contracts import AgentTrace, SingleAgentDecision
from fault_diagnosis.single_agent.intent import fallback_understanding_payload
from fault_diagnosis.single_agent.output.payloads import build_direct_complete_payload
from fault_diagnosis.single_agent.workflow import (
    TaskRoute,
    TaskType,
    build_goal_set,
    build_workflow_plan,
    route_task,
    summarize_goal_set,
)


def _goal_types(goal_set) -> list[str]:
    return [goal.goal_type for goal in goal_set.goals]


def _goal(goal_set, goal_type: str):
    return next(goal for goal in goal_set.goals if goal.goal_type == goal_type)


def test_composite_question_builds_structured_goals() -> None:
    message = "这个 A07089 是什么意思？现在设备有没有故障？怎么处理？"
    payload = fallback_understanding_payload(message, "维修员")
    goal_set = build_goal_set(
        message=message,
        payload=payload,
        resolved_context={},
        route_hint={
            "task_type": TaskType.ALARM_TRIAGE,
            "requested_output": "answer",
            "legacy_intent_candidates": [
                "explain_alarm_code",
                "check_current_status",
                "resolution_recommendation",
            ],
        },
    )

    assert _goal_types(goal_set) == [
        "check_runtime_status",
        "explain_fault_code",
        "diagnose_fault",
        "recommend_resolution",
    ]
    assert goal_set.goals
    assert "explain_alarm_code" in goal_set.intent_stack_projection
    assert "check_current_status" in goal_set.intent_stack_projection
    assert "resolution_recommendation" in goal_set.intent_stack_projection


def test_report_artifact_workorder_followup_builds_workorder_goal() -> None:
    message = "从结果来看是不是要生成工单？"
    payload = fallback_understanding_payload(message, "维修员")
    context = ResolvedContext(
        relation_to_previous="action_followup",
        referenced_artifact_id="eb_J1",
        inherited_slots={"device": "J1", "evidence_bundle": "eb_J1"},
        evidence_mode="reuse_previous_artifact",
    )

    goal_set = build_goal_set(
        message=message,
        payload=payload,
        resolved_context=context,
        route_hint={"legacy_intent_candidates": ["workorder_decision"]},
    )

    assert "decide_workorder" in _goal_types(goal_set)
    assert {"assess_severity", "diagnose_fault"}.intersection(_goal_types(goal_set))
    assert _goal(goal_set, "decide_workorder").context_refs == ["eb_J1"]
    assert _goal(goal_set, "decide_workorder").risk_level == "requires_confirmation"


def test_stale_workorder_depends_on_refresh_goal_id() -> None:
    message = "要不要生成工单？"
    payload = fallback_understanding_payload(message, "维修员")
    context = ResolvedContext(
        relation_to_previous="action_followup",
        referenced_artifact_id="eb_stale",
        inherited_slots={"device": "J1", "evidence_bundle": "eb_stale"},
        stale_evidence=True,
        should_refresh_runtime_data=True,
        evidence_mode="reuse_and_refresh_status",
    )

    goal_set = build_goal_set(
        message=message,
        payload=payload,
        resolved_context=context,
        route_hint={"legacy_intent_candidates": ["workorder_decision"]},
    )

    refresh = _goal(goal_set, "refresh_current_status")
    workorder = _goal(goal_set, "decide_workorder")
    assert workorder.depends_on == [refresh.goal_id]
    assert "latest_realtime_status" in workorder.required_evidence
    assert workorder.risk_level == "requires_confirmation"
    assert "dispatch" not in workorder.reason.lower()


def test_report_handoff_builds_generate_report_goal() -> None:
    goal_set = build_goal_set(
        message="基于刚才结果导出报告",
        payload={},
        resolved_context=ResolvedContext(
            relation_to_previous="report_handoff",
            referenced_artifact_id="eb_J1",
            inherited_slots={"device": "J1", "evidence_bundle": "eb_J1"},
        ),
        route_hint={"requested_output": "report", "legacy_intent_candidates": ["report_generation"]},
    )

    assert _goal_types(goal_set) == ["generate_report"]
    assert goal_set.primary_goal_id == _goal(goal_set, "generate_report").goal_id


def test_ambiguous_reference_builds_clarification_and_blocked_goal() -> None:
    goal_set = build_goal_set(
        message="它严重吗？",
        payload={},
        resolved_context=ResolvedContext(
            relation_to_previous="ambiguous",
            missing_context=["请确认“它”指的是哪个设备。"],
            candidates={"assets": ["J1", "J2"]},
        ),
        route_hint={"legacy_intent_candidates": ["severity_assessment"]},
    )

    assert _goal(goal_set, "clarify_missing_context").status == "ready"
    assert _goal(goal_set, "assess_severity").status == "blocked"
    assert goal_set.primary_goal_id == _goal(goal_set, "clarify_missing_context").goal_id
    assert goal_set.blocked_goals == [_goal(goal_set, "assess_severity").goal_id]


def test_explicit_device_switch_does_not_reference_previous_artifact() -> None:
    message = "J2 当前状态怎么样"
    payload = fallback_understanding_payload(message, "维修员")
    context = ResolvedContext(
        relation_to_previous="new_case",
        referenced_artifact_id=None,
        inherited_slots={},
        active_asset="J2",
    )

    goal_set = build_goal_set(
        message=message,
        payload=payload,
        resolved_context=context,
        route_hint={"task_type": TaskType.STATUS_QUERY, "legacy_intent_candidates": ["check_current_status"]},
    )

    assert "check_runtime_status" in _goal_types(goal_set)
    assert all("J1" not in ref and ref != "eb_J1" for goal in goal_set.goals for ref in goal.context_refs)


def test_fallback_goal_set_is_never_empty() -> None:
    goal_set = build_goal_set(
        message="嗯",
        payload={},
        resolved_context={},
        route_hint={"task_type": TaskType.STATUS_QUERY},
    )

    assert goal_set.goals
    assert goal_set.primary_goal_id


def test_route_intent_stack_merges_projection_and_legacy() -> None:
    message = "帮我直接派发 J1 工单"
    payload = fallback_understanding_payload(message, "维修员")
    route = route_task(payload=payload, message=message)

    assert "workorder_decision" in route.goal_set["intent_stack_projection"]
    assert "dispatch_workorder" in route.intent_stack
    assert "workorder_decision" in route.intent_stack
    assert route.flags["goal_projection_mismatch"] is True


def test_workflow_policy_consumes_intent_stack_not_goals() -> None:
    goal_set = {
        "intent_stack_projection": ["workorder_decision"],
        "goals": [{"goal_id": "goal_1_decide_workorder", "goal_type": "decide_workorder"}],
    }
    baseline_route = TaskRoute(
        primary_task_type=TaskType.FAULT_DIAGNOSIS,
        intent_stack=[],
        objects={"device_ids": ["J1"]},
    )
    route_with_goals_only = TaskRoute(
        primary_task_type=TaskType.FAULT_DIAGNOSIS,
        intent_stack=[],
        goal_set=goal_set,
        objects={"device_ids": ["J1"]},
    )
    baseline = build_workflow_plan(baseline_route)
    plan = build_workflow_plan(route_with_goals_only)

    assert plan.resolved_nodes == baseline.resolved_nodes
    assert plan.runtime_tools == baseline.runtime_tools

    route_with_intent = TaskRoute(
        primary_task_type=TaskType.FAULT_DIAGNOSIS,
        intent_stack=["workorder_decision"],
        goal_set=goal_set,
        objects={"device_ids": ["J1"]},
        flags={"need_workorder_decision": True},
        action_target="workorder",
    )
    plan_with_intent = build_workflow_plan(route_with_intent)

    assert plan_with_intent.resolved_nodes["workorder_decision"] is True


def test_goals_do_not_enable_tools_without_legacy_policy_inputs() -> None:
    baseline_route = TaskRoute(
        primary_task_type=TaskType.STATUS_QUERY,
        intent_stack=[],
    )
    route = TaskRoute(
        primary_task_type=TaskType.STATUS_QUERY,
        intent_stack=[],
        goal_set={
            "intent_stack_projection": ["check_current_status", "report_generation"],
            "goals": [
                {"goal_id": "goal_1_check_runtime_status", "goal_type": "check_runtime_status"},
                {"goal_id": "goal_2_generate_report", "goal_type": "generate_report"},
            ],
        },
    )

    baseline = build_workflow_plan(baseline_route, needs_report=False)
    plan = build_workflow_plan(route, needs_report=False)

    assert plan.runtime_tools == baseline.runtime_tools
    assert plan.resolved_nodes == baseline.resolved_nodes


def test_complete_top_level_goal_set_is_compact_but_decision_keeps_full_structure() -> None:
    decision = SingleAgentDecision(
        goal_set={
            "primary_goal_id": "goal_1_diagnose_fault",
            "execution_order": ["goal_1_diagnose_fault"],
            "blocked_goals": [],
            "intent_stack_projection": ["fault_diagnosis"],
            "goal_summary": "safe summary",
            "goals": [
                {
                    "goal_id": "goal_1_diagnose_fault",
                    "goal_type": "diagnose_fault",
                    "status": "ready",
                    "reason": "SELECT * FROM hidden_table; long report body",
                    "context_refs": ["artifact.secret"],
                }
            ],
        }
    )
    payload = build_direct_complete_payload(
        thread_id="thread.goal",
        trace_id="trace.goal",
        request_id="request.goal",
        final_answer="ok",
        decision=decision,
        trace=AgentTrace(
            trace_id="trace.goal",
            request_id="request.goal",
            thread_id="thread.goal",
            user_identity="tester",
            user_message="test",
        ),
        event_count=0,
    )

    assert payload["goal_set"] == {
        "primary_goal_id": "goal_1_diagnose_fault",
        "goal_types": ["diagnose_fault"],
        "execution_order": ["goal_1_diagnose_fault"],
        "blocked_goals": [],
        "intent_stack_projection": ["fault_diagnosis"],
        "goal_summary": "safe summary",
    }
    assert "SELECT *" not in str(payload["goal_set"])
    assert "SELECT *" in str(payload["decision"]["goal_set"])


def test_goal_summary_for_trace_omits_long_goal_payload() -> None:
    summary = summarize_goal_set(
        {
            "primary_goal_id": "goal_1_generate_report",
            "execution_order": ["goal_1_generate_report"],
            "blocked_goals": [],
            "intent_stack_projection": ["report_generation"],
            "goal_summary": "report goal",
            "goals": [
                {
                    "goal_id": "goal_1_generate_report",
                    "goal_type": "generate_report",
                    "reason": "SELECT * FROM real_data_01; 报告正文" * 20,
                    "required_evidence": ["long evidence body"],
                }
            ],
        }
    )

    assert summary["goal_types"] == ["generate_report"]
    assert "SELECT *" not in str(summary)
    assert "报告正文" not in str(summary)
