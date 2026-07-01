from __future__ import annotations

from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.compat import project_task_type_for_compat
from fault_diagnosis.single_agent.intent import fallback_understanding_payload
from fault_diagnosis.single_agent.planning import apply_planner_gate_to_decision, build_planner_gate
from fault_diagnosis.single_agent.workflow import TaskRoute, TaskType, build_workflow_plan, route_task
from fault_diagnosis.single_agent.workflow.policies import select_policy_from_intent_axes

_PRIMARY = "primary" + "_task_type"


def _route(message: str) -> TaskRoute:
    return route_task(payload=fallback_understanding_payload(message, "维修员"), message=message)


def _enabled(plan, *nodes: str) -> None:
    for node in nodes:
        assert plan.resolved_nodes[node] is True


def _runtime_tools(plan) -> set[str]:
    return set(plan.runtime_tools)


def test_goal_axes_route_knowledge_qa_to_knowledge_node_without_sql() -> None:
    plan = build_workflow_plan(_route("A07089 是什么意思"))

    _enabled(plan, "knowledge", "analysis")
    assert plan.resolved_nodes["sql"] is False
    assert _runtime_tools(plan) == {"query_knowledge_base"}


def test_goal_axes_route_status_query_to_sql_node() -> None:
    plan = build_workflow_plan(_route("J1 当前运行状态怎么样"))

    _enabled(plan, "sql", "analysis")
    assert plan.resolved_nodes["knowledge"] is False
    assert _runtime_tools(plan) == {"sql_db_query_checker", "sql_db_query"}


def test_goal_axes_route_alarm_triage_to_sql_knowledge_analysis_without_tool_expansion() -> None:
    route = _route("J1 的 A07089 现在还在报警吗，怎么处理")
    plan = build_workflow_plan(route)

    _enabled(plan, "sql", "knowledge", "analysis", "resolution_recommendation")
    assert _runtime_tools(plan) <= {"sql_db_query_checker", "sql_db_query", "query_knowledge_base", "save_report"}
    assert "save_report" not in _runtime_tools(plan)
    assert project_task_type_for_compat(route)[_PRIMARY] == TaskType.ALARM_TRIAGE.value
    assert route.intent_stack


def test_goal_axes_route_report_generation_keeps_report_node_and_legacy_output_fields() -> None:
    route = _route("生成 J1 的运行报告")
    plan = build_workflow_plan(route)

    _enabled(plan, "report")
    assert "save_report" in _runtime_tools(plan)
    assert plan.metadata[_PRIMARY] == "report_generation"
    assert "candidate_task_types" in plan.metadata
    assert "intent_stack" in plan.metadata


def test_policy_selector_falls_back_to_legacy_when_axes_would_expand_behavior() -> None:
    route = TaskRoute.model_validate(
        {
            _PRIMARY: TaskType.STATUS_QUERY,
            "task_family": "reporting",
            "requested_output": "report",
            "goal_set": {"goals": [{"goal_type": "generate_report"}]},
            "objects": {"device_ids": ["J1"]},
            "flags": {"need_sql": True},
        }
    )

    policy = select_policy_from_intent_axes(route)
    plan = build_workflow_plan(route)
    baseline = build_workflow_plan(
        TaskRoute.model_validate(
            {
                _PRIMARY: TaskType.STATUS_QUERY,
                "objects": {"device_ids": ["J1"]},
                "flags": {"need_sql": True},
            }
        )
    )

    assert policy.task_type == TaskType.STATUS_QUERY
    assert plan.policy.task_type == TaskType.STATUS_QUERY
    assert plan.resolved_nodes == baseline.resolved_nodes
    assert plan.runtime_tools == baseline.runtime_tools


def test_workorder_action_policy_stays_guarded_and_planner_gate_dry_run_only() -> None:
    route = TaskRoute.model_validate(
        {
            _PRIMARY: TaskType.ACTION_REQUEST,
            "task_family": "action_or_workorder",
            "goal_set": {"goals": [{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}]},
            "objects": {"device_ids": ["J1"], "alarm_codes": ["A07089"]},
            "flags": {"need_workorder_decision": True, "need_sql": True, "need_knowledge": True},
            "action_target": "workorder",
            "action_type": "create_workorder_draft",
            "risk_level": "requires_confirmation",
        }
    )
    plan = build_workflow_plan(route)

    _enabled(plan, "permission_check", "risk_check", "workorder_decision", "audit_log")
    assert _runtime_tools(plan) <= {"sql_db_query_checker", "sql_db_query", "query_knowledge_base", "save_report"}

    decision = SingleAgentDecision.model_validate(
        {
            _PRIMARY: "action_request",
            "task_family": "action_or_workorder",
            "goal_set": route.goal_set,
            "enabled_nodes": plan.resolved_nodes,
            "runtime_tools": plan.runtime_tools,
            "action_target": "workorder",
            "action_type": "create_workorder_draft",
            "risk_level": "requires_confirmation",
        }
    )
    before_nodes = dict(decision.enabled_nodes)
    before_tools = list(decision.runtime_tools)
    shadow = {
        "nodes": [{"node": node, "desired_state": "enabled"} for node, enabled in before_nodes.items() if enabled],
        "tool_plan": {"authorized_runtime_tools": list(before_tools)},
        "output_plan": {"expected_output": "workorder_decision", "required_disclosures": []},
    }
    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False, "diagnosis_dry_run": True, "diagnosis_active": True},
    )
    apply_planner_gate_to_decision(decision, gate)

    assert gate.selected_execution_source == "legacy_policy"
    assert gate.safety_summary["workorder_action_readiness"]["dry_run_only"] is True
    assert decision.enabled_nodes == before_nodes
    assert decision.runtime_tools == before_tools
