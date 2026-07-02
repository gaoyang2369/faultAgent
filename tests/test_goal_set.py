from __future__ import annotations

from fault_diagnosis.context import ResolvedContext
from fault_diagnosis.single_agent.contracts import AgentTrace, SingleAgentDecision
from fault_diagnosis.single_agent.intent import fallback_understanding_payload
from fault_diagnosis.single_agent.output.payloads import build_direct_complete_payload
from fault_diagnosis.single_agent.workflow import TaskRoute, build_goal_set, build_workflow_plan, route_task, summarize_goal_set


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
            "requested_output": "answer",
            "legacy_candidates": ["explain_alarm_code", "check_current_status", "resolution_recommendation"],
        },
    )

    assert _goal_types(goal_set) == [
        "check_runtime_status",
        "explain_fault_code",
        "diagnose_fault",
        "recommend_resolution",
    ]
    assert goal_set.goals
    assert "explain_alarm_code" in goal_set.legacy_intent_projection
    assert "check_current_status" in goal_set.legacy_intent_projection
    assert "resolution_recommendation" in goal_set.legacy_intent_projection


def test_report_artifact_workorder_followup_builds_workorder_goal() -> None:
    goal_set = build_goal_set(
        message="从结果来看是不是要生成工单？",
        payload={},
        resolved_context=ResolvedContext(
            relation_to_previous="action_followup",
            referenced_artifact_id="eb_J1",
            inherited_slots={"device": "J1", "evidence_bundle": "eb_J1"},
            evidence_mode="reuse_previous_artifact",
        ),
        route_hint={"legacy_candidates": ["workorder_decision"]},
    )

    assert "decide_workorder" in _goal_types(goal_set)
    assert _goal(goal_set, "decide_workorder").context_refs == ["eb_J1"]
    assert _goal(goal_set, "decide_workorder").risk_level == "requires_confirmation"


def test_stale_workorder_depends_on_refresh_goal_id() -> None:
    goal_set = build_goal_set(
        message="要不要生成工单？",
        payload={},
        resolved_context=ResolvedContext(
            relation_to_previous="action_followup",
            referenced_artifact_id="eb_stale",
            inherited_slots={"device": "J1", "evidence_bundle": "eb_stale"},
            stale_evidence=True,
            should_refresh_runtime_data=True,
            evidence_mode="reuse_and_refresh_status",
        ),
        route_hint={"legacy_candidates": ["workorder_decision"]},
    )

    refresh = _goal(goal_set, "refresh_current_status")
    workorder = _goal(goal_set, "decide_workorder")
    assert workorder.depends_on == [refresh.goal_id]
    assert "latest_realtime_status" in workorder.required_evidence
    assert workorder.risk_level == "requires_confirmation"


def test_report_handoff_builds_generate_report_goal() -> None:
    goal_set = build_goal_set(
        message="基于刚才结果导出报告",
        payload={},
        resolved_context=ResolvedContext(
            relation_to_previous="report_handoff",
            referenced_artifact_id="eb_J1",
            inherited_slots={"device": "J1", "evidence_bundle": "eb_J1"},
        ),
        route_hint={"requested_output": "report", "legacy_candidates": ["report_generation"]},
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
        route_hint={"legacy_candidates": ["severity_assessment"]},
    )

    assert _goal(goal_set, "clarify_missing_context").status == "ready"
    assert _goal(goal_set, "assess_severity").status == "blocked"
    assert goal_set.primary_goal_id == _goal(goal_set, "clarify_missing_context").goal_id
    assert goal_set.blocked_goals == [_goal(goal_set, "assess_severity").goal_id]


def test_route_builds_goal_set_without_legacy_route_fields() -> None:
    route = route_task(payload=fallback_understanding_payload("J2 当前状态怎么样", "维修员"), message="J2 当前状态怎么样")

    assert route.task_family == "runtime_status"
    assert "check_runtime_status" in summarize_goal_set(route.goal_set)["goal_types"]
    assert all("J1" not in ref and ref != "eb_J1" for goal in route.goals for ref in goal.context_refs)


def test_fallback_goal_set_is_never_empty() -> None:
    goal_set = build_goal_set(message="嗯", payload={}, resolved_context={}, route_hint={})

    assert goal_set.goals
    assert goal_set.primary_goal_id


def test_workflow_policy_consumes_goals() -> None:
    route = TaskRoute(
        task_family="action_or_workorder",
        action_target="workorder",
        goal_set={"goals": [{"goal_id": "goal_1_decide_workorder", "goal_type": "decide_workorder"}]},
        objects={"device_ids": ["J1"]},
        flags={"need_workorder_decision": True},
    )
    plan = build_workflow_plan(route)

    assert plan.policy.policy_id == "action_request_v1"
    assert plan.resolved_nodes["workorder_decision"] is True


def test_goals_are_policy_inputs_without_legacy_task_fallback() -> None:
    route = TaskRoute(
        task_family="reporting",
        requested_output="report",
        goal_set={
            "legacy_intent_projection": ["check_current_status", "report_generation"],
            "goals": [
                {"goal_id": "goal_1_check_runtime_status", "goal_type": "check_runtime_status"},
                {"goal_id": "goal_2_generate_report", "goal_type": "generate_report"},
            ],
        },
    )

    plan = build_workflow_plan(route, needs_report=False)

    assert plan.policy.policy_id == "report_generation_v1"
    assert plan.resolved_nodes["report"] is True
    assert "save_report" in plan.runtime_tools


def test_complete_top_level_goal_set_is_compact_but_decision_keeps_full_structure() -> None:
    decision = SingleAgentDecision(
        goal_set={
            "primary_goal_id": "goal_1_diagnose_fault",
            "execution_order": ["goal_1_diagnose_fault"],
            "blocked_goals": [],
            "legacy_intent_projection": ["fault_diagnosis"],
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
        "legacy_intent_projection": ["fault_diagnosis"],
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
            "legacy_intent_projection": ["report_generation"],
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
