from __future__ import annotations

import inspect
from pathlib import Path

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.contracts import AgentTrace
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.output.payloads import build_direct_complete_payload
from fault_diagnosis.single_agent.workflow import build_workflow_plan, route_task
from fault_diagnosis.single_agent.workflow import policies as workflow_policies
from scripts.goal_native_cutover_check import run_check
from scripts.legacy_dependency_scan import run_scan


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


def _decision(message: str):
    payload = fallback_understanding_payload(message, "维修员")
    return decide_capabilities(payload=payload, request=_request(message, payload), message=message, report_from_previous_artifact=False)


def test_policy_main_node_resolution_uses_goal_axes() -> None:
    source = inspect.getsource(workflow_policies.resolve_nodes_from_goals)
    resolver = inspect.getsource(workflow_policies._resolve_node)

    assert "goal_types(route)" in source
    assert "goal_types(route)" in resolver


def test_complete_payload_keeps_compat_fields_at_output_boundary() -> None:
    decision = _decision("J1 的 A07089 现在还在报警吗，怎么处理")
    payload = build_direct_complete_payload(
        thread_id="thread.phase60",
        trace_id="trace.phase60",
        request_id="request.phase60",
        final_answer="ok",
        decision=decision,
        trace=AgentTrace(
            trace_id="trace.phase60",
            request_id="request.phase60",
            thread_id="thread.phase60",
            user_identity="tester",
            user_message="test",
        ),
        event_count=0,
    )

    assert payload["decision"]["primary_task_type"] in {"fault_diagnosis", "knowledge_qa", "status_query"}
    assert "candidate_task_types" in payload["decision"]
    assert "intent_stack" in payload["decision"]
    assert payload["workflow_route"]["primary_task_type"] == payload["decision"]["primary_task_type"]


def test_goalset_policy_ignores_retired_planning_payloads() -> None:
    message = "J1 的 A07089 现在还在报警吗，怎么处理"
    payload = fallback_understanding_payload(message, "维修员")
    route = route_task(payload=payload, message=message)
    baseline = build_workflow_plan(route)
    mutated = route.model_copy(deep=True)
    mutated.goal_set["shadow_plan"] = {"enabled_node_names": ["report"], "authorized_runtime_tools": ["save_report"]}

    changed = build_workflow_plan(mutated)

    assert changed.resolved_nodes == baseline.resolved_nodes
    assert changed.runtime_tools == baseline.runtime_tools
    assert changed.policy.policy_id == baseline.policy.policy_id


def test_goal_native_cutover_scripts_report_no_internal_forbidden_hits() -> None:
    assert run_check(ROOT)["summary"]["internal_forbidden_hits"] == 0
    assert run_scan(ROOT)["summary"]["internal_forbidden_hits"] == 0
