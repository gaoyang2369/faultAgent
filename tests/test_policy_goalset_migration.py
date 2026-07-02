from __future__ import annotations

from fault_diagnosis.single_agent.intent import fallback_understanding_payload
from fault_diagnosis.single_agent.workflow import TaskRoute, build_workflow_plan, route_task
from fault_diagnosis.single_agent.workflow.policies import select_policy_from_intent_axes


def _route(message: str) -> TaskRoute:
    return route_task(payload=fallback_understanding_payload(message, "维修员"), message=message)


def _runtime_tools(route: TaskRoute) -> set[str]:
    return set(build_workflow_plan(route).runtime_tools)


def test_goal_axes_route_knowledge_qa_to_knowledge_node_without_sql() -> None:
    plan = build_workflow_plan(_route("A07089 是什么意思"))

    assert plan.policy.policy_id == "knowledge_qa_v1"
    assert plan.resolved_nodes["knowledge"] is True
    assert plan.resolved_nodes["sql"] is False
    assert set(plan.runtime_tools) == {"query_knowledge_base"}


def test_goal_axes_route_status_query_to_sql_node() -> None:
    plan = build_workflow_plan(_route("J1 当前运行状态怎么样"))

    assert plan.policy.policy_id == "status_query_v1"
    assert plan.resolved_nodes["sql"] is True
    assert plan.resolved_nodes["knowledge"] is False
    assert set(plan.runtime_tools) == {"sql_db_query_checker", "sql_db_query"}


def test_policy_selector_uses_goal_axes_without_legacy_fallback() -> None:
    route = TaskRoute(
        task_family="reporting",
        requested_output="report",
        goal_set={"goals": [{"goal_type": "generate_report"}]},
        objects={"device_ids": ["J1"]},
        flags={"need_sql": True},
    )

    policy = select_policy_from_intent_axes(route)
    plan = build_workflow_plan(route)

    assert policy.policy_id == "report_generation_v1"
    assert plan.policy.policy_id == "report_generation_v1"
    assert plan.resolved_nodes["report"] is True
    assert set(plan.runtime_tools) == {"sql_db_query_checker", "sql_db_query", "save_report"}


def test_goal_axes_workorder_policy_stays_draft_only() -> None:
    route = TaskRoute(
        task_family="action_or_workorder",
        goal_set={"goals": [{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}]},
        objects={"device_ids": ["J1"], "alarm_codes": ["A07089"]},
        flags={"need_workorder_decision": True, "need_sql": True, "need_knowledge": True},
        action_target="workorder",
        action_type="create_workorder_draft",
        risk_level="requires_confirmation",
    )
    plan = build_workflow_plan(route)

    assert plan.policy.policy_id == "action_request_v1"
    assert plan.resolved_nodes["permission_check"] is True
    assert plan.resolved_nodes["risk_check"] is True
    assert plan.resolved_nodes["workorder_decision"] is True
    assert "device_control.write" in plan.policy.forbidden_tools
    assert "workorder.dispatch" in plan.policy.forbidden_tools
    assert _runtime_tools(route) <= {"sql_db_query_checker", "sql_db_query", "query_knowledge_base", "save_report"}
