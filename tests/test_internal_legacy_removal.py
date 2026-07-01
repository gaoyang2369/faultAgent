from __future__ import annotations

import inspect
from pathlib import Path

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.contracts import AgentTrace, SingleAgentDecision
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.output.payloads import build_direct_complete_payload
from fault_diagnosis.single_agent.planning import apply_planner_gate_to_decision, build_planner_gate
from fault_diagnosis.single_agent.workflow import build_workflow_plan, route_task
from fault_diagnosis.single_agent.workflow import policies as workflow_policies
from scripts.legacy_dependency_scan import run_scan
from scripts.legacy_deprecation_check import run_check


ROOT = Path(__file__).resolve().parents[1]


def _request(message: str, payload: dict) -> DiagnosisRequest:
    return DiagnosisRequest(
        user_message=message,
        user_identity="维修员",
        equipment_hint=payload.get("equipment_hint"),
        metric_hint=payload.get("metric_hint"),
        fault_code_hint=payload.get("fault_code_hint"),
        time_range_hint=payload.get("time_range_hint"),
        needs_report=bool(payload.get("needs_report")),
        report_format="markdown",
        analysis_goal=str(payload.get("analysis_goal") or message),
    )


def _decision(message: str) -> SingleAgentDecision:
    payload = fallback_understanding_payload(message, "维修员")
    return decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )


def test_policy_main_node_resolution_uses_goal_axes_not_legacy_intents() -> None:
    source = inspect.getsource(workflow_policies.resolve_nodes_from_goals)
    resolver = inspect.getsource(workflow_policies._resolve_node)

    assert "intent_stack" not in source
    assert "primary_task_type" not in source
    assert "intent_stack" not in resolver
    assert "primary_task_type" not in resolver
    assert "goal_types(route)" in source
    assert "goal_types(route)" in resolver


def test_public_compatibility_fields_still_exist_after_internal_removal() -> None:
    decision = _decision("J1 的 A07089 现在还在报警吗，怎么处理")
    payload = build_direct_complete_payload(
        thread_id="thread.phase54",
        trace_id="trace.phase54",
        request_id="request.phase54",
        final_answer="ok",
        decision=decision,
        trace=AgentTrace(
            trace_id="trace.phase54",
            request_id="request.phase54",
            thread_id="thread.phase54",
            user_identity="tester",
            user_message="test",
        ),
        event_count=0,
    )

    assert payload["decision"]["primary_task_type"] == "alarm_triage"
    assert "candidate_task_types" in payload["decision"]
    assert "intent_stack" in payload["decision"]
    assert payload["workflow_route"]["primary_task_type"] == "alarm_triage"
    assert payload["workflow_route"]["intent_stack"] == decision.intent_stack


def test_goalset_policy_migration_keeps_nodes_and_runtime_tools_stable() -> None:
    message = "J1 的 A07089 现在还在报警吗，怎么处理"
    payload = fallback_understanding_payload(message, "维修员")
    route = route_task(payload=payload, message=message)
    baseline = build_workflow_plan(route)
    mutated = route.model_copy(deep=True)
    mutated.goal_set["shadow_plan"] = {"enabled_node_names": ["report"], "authorized_runtime_tools": ["save_report"]}

    changed = build_workflow_plan(mutated)

    assert changed.resolved_nodes == baseline.resolved_nodes
    assert changed.runtime_tools == baseline.runtime_tools


def test_high_risk_action_workorder_remains_dry_run_only() -> None:
    decision = SingleAgentDecision.model_validate(
        {
            "primary_task_type": "action_request",
            "task_family": "action_or_workorder",
            "goal_set": {"goals": [{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}]},
            "enabled_nodes": {
                "permission_check": True,
                "risk_check": True,
                "sql": True,
                "knowledge": True,
                "analysis": True,
                "workorder_decision": True,
                "output_guardrail": True,
                "audit_log": True,
            },
            "runtime_tools": ["sql_db_query_checker", "sql_db_query", "query_knowledge_base"],
            "authorization": {"mode": "allow"},
            "action_type": "create_workorder_draft",
            "action_target": "workorder",
            "risk_level": "requires_confirmation",
            "satisfied_evidence": ["diagnosis_summary", "severity_or_status_level", "key_evidence", "recommended_action_policy"],
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


def test_phase5_4_legacy_scan_and_deprecation_guard() -> None:
    scan = run_scan(ROOT)["summary"]
    check = run_check(ROOT)["summary"]

    assert scan["task_type_read_files"] <= 20
    assert scan["task_type_write_files"] <= 20
    assert scan["intent_stack_read_files"] <= 10
    assert scan["intent_stack_write_files"] <= 10
    assert scan["policy_dependency_files"] <= 1
    assert check["disallowed_dependency_hits"] == 0
