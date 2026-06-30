from __future__ import annotations

from pathlib import Path

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.workflow import TaskRoute, TaskType, build_workflow_plan, route_task


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
    return decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )


def test_legacy_task_type_and_intent_stack_key_intents_are_retained() -> None:
    cases = [
        ("J1 当前运行状态怎么样", "status_query", ["check_current_status"]),
        ("A07089 是什么意思", "knowledge_qa", ["explain_alarm_code"]),
        ("J1 的 A07089 现在还在报警吗，怎么处理", "alarm_triage", ["explain_alarm_code", "check_current_status", "resolution_recommendation"]),
        ("诊断 J1 A07089 的原因", "fault_diagnosis", ["explain_alarm_code", "fault_diagnosis"]),
        ("生成 J1 的运行报告", "report_generation", ["report_generation"]),
        ("帮我重启 J1", "action_request", ["action_request"]),
    ]

    for message, task_type, required_intents in cases:
        decision = _decision(message)
        assert decision.primary_task_type == task_type
        for intent in required_intents:
            assert intent in decision.intent_stack


def test_task_family_changes_do_not_change_enabled_nodes_or_runtime_tools() -> None:
    payload = fallback_understanding_payload("J1 的 A07089 现在还在报警吗，怎么处理", "维修员")
    route = route_task(payload=payload, message="J1 的 A07089 现在还在报警吗，怎么处理")
    baseline = build_workflow_plan(route)
    route_with_different_family = TaskRoute.model_validate(
        {
            **route.model_dump(),
            "task_family": "meta",
            "task_family_reason": "test-only mutation",
            "task_family_source": "unknown_fallback",
            "task_family_warnings": ["test_only"],
        }
    )
    mutated = build_workflow_plan(route_with_different_family)

    assert route.primary_task_type == TaskType.ALARM_TRIAGE
    assert route_with_different_family.primary_task_type == route.primary_task_type
    assert route_with_different_family.intent_stack == route.intent_stack
    assert mutated.resolved_nodes == baseline.resolved_nodes
    assert mutated.runtime_tools == baseline.runtime_tools
    assert mutated.policy.policy_id == baseline.policy.policy_id


def test_build_workflow_plan_does_not_read_task_family() -> None:
    route = TaskRoute(
        primary_task_type=TaskType.STATUS_QUERY,
        intent_stack=["check_current_status"],
        task_family="runtime_status",
        objects={"device_ids": ["J1"]},
        flags={"need_sql": True},
    )
    baseline = build_workflow_plan(route)
    route.task_family = "action_or_workorder"
    route.task_family_reason = "test-only mutation"
    changed = build_workflow_plan(route)

    assert changed.resolved_nodes == baseline.resolved_nodes
    assert changed.runtime_tools == baseline.runtime_tools


def test_build_workflow_plan_does_not_read_shadow_plan() -> None:
    route = TaskRoute(
        primary_task_type=TaskType.FAULT_DIAGNOSIS,
        intent_stack=["fault_diagnosis"],
        objects={"device_ids": ["J1"], "alarm_codes": ["A07089"]},
        flags={"need_sql": True, "need_knowledge": True},
    )
    baseline = build_workflow_plan(route)
    route.goal_set["shadow_plan"] = {"enabled_node_names": ["report"], "authorized_runtime_tools": ["save_report"]}
    changed = build_workflow_plan(route)

    assert changed.resolved_nodes == baseline.resolved_nodes
    assert changed.runtime_tools == baseline.runtime_tools


def test_execution_policy_and_tools_do_not_reference_task_family() -> None:
    paths = [
        ROOT / "fault_diagnosis/single_agent/workflow/policies.py",
        ROOT / "fault_diagnosis/single_agent/workflow/evidence_gap.py",
        ROOT / "fault_diagnosis/single_agent/stages.py",
        ROOT / "fault_diagnosis/single_agent/sql_safety.py",
        ROOT / "fault_diagnosis/single_agent/workorder_suggestions.py",
    ]
    tool_paths = sorted((ROOT / "fault_diagnosis/tools").glob("**/*.py"))
    for path in [*paths, *tool_paths]:
        assert "task_family" not in path.read_text(encoding="utf-8"), str(path)


def test_execution_policy_runner_and_tools_do_not_reference_shadow_plan() -> None:
    paths = [
        ROOT / "fault_diagnosis/single_agent/workflow/policies.py",
        ROOT / "fault_diagnosis/single_agent/workflow/evidence_gap.py",
        ROOT / "fault_diagnosis/single_agent/stages.py",
    ]
    tool_paths = sorted((ROOT / "fault_diagnosis/tools").glob("**/*.py"))
    for path in [*paths, *tool_paths]:
        assert "shadow_plan" not in path.read_text(encoding="utf-8"), str(path)
    runner_text = (ROOT / "fault_diagnosis/single_agent/runner.py").read_text(encoding="utf-8")
    tool_call_section = runner_text[runner_text.index("    def _start_tool_call"):]
    assert "shadow_plan" not in tool_call_section
