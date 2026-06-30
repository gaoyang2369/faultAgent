from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.security.contracts import AuthContext
from fault_diagnosis.single_agent.contracts import AgentTrace, SingleAgentDecision
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.output.payloads import build_direct_complete_payload
from fault_diagnosis.single_agent.planner import build_plan_snapshot
from fault_diagnosis.single_agent.workflow import (
    PUBLIC_TASK_FAMILIES,
    TaskRoute,
    TaskType,
    build_workflow_plan,
    resolve_task_family,
    route_task,
)


def _request(message: str, payload: dict) -> DiagnosisRequest:
    return DiagnosisRequest(
        user_message=message,
        user_identity="游客",
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


def test_task_type_to_task_family_mapping() -> None:
    cases = {
        "A07089 是什么意思": ("knowledge_qa", "knowledge_lookup"),
        "J1 当前运行状态怎么样": ("status_query", "runtime_status"),
        "J1 的 A07089 现在还在报警吗，怎么处理": ("alarm_triage", "diagnosis"),
        "诊断 J1 A07089 的原因": ("fault_diagnosis", "diagnosis"),
        "对 J1 的异常做根因分析": ("root_cause_analysis", "diagnosis"),
        "评估 J1 最近健康风险": ("health_assessment", "diagnosis"),
        "生成 J1 的运行报告": ("report_generation", "reporting"),
        "帮我重启 J1": ("action_request", "action_or_workorder"),
        "我这个身份可以访问到哪些设备呀？": ("permission_scope_query", "meta"),
    }

    for message, (task_type, task_family) in cases.items():
        decision = _decision(message)
        assert decision.primary_task_type == task_type
        assert decision.task_family == task_family
        assert decision.task_family in PUBLIC_TASK_FAMILIES


def test_direct_and_meta_markers_map_to_meta() -> None:
    for marker in ("direct_response", "greeting", "thanks", "capability", "permission_scope_query"):
        resolution = resolve_task_family(
            task_type=marker,
            requested_output=None,
            goals=[],
            resolved_context={},
            intent_stack=[],
        )
        assert resolution.task_family == "meta"
        assert resolution.task_family in PUBLIC_TASK_FAMILIES


def test_unknown_task_type_falls_back_without_public_unknown_family() -> None:
    resolution = resolve_task_family(
        task_type="new_future_task",
        requested_output=None,
        goals=[],
        resolved_context={},
        intent_stack=[],
    )

    assert resolution.task_family == "meta"
    assert resolution.source == "unknown_fallback"
    assert "unknown_task_type" in resolution.warnings
    assert resolution.task_family in PUBLIC_TASK_FAMILIES
    assert resolution.task_family not in {"unknown", "new_future_task", None}


def test_task_family_mismatch_is_debug_only_and_does_not_change_policy_or_tools() -> None:
    baseline_route = TaskRoute(primary_task_type=TaskType.STATUS_QUERY, intent_stack=[])
    baseline = build_workflow_plan(baseline_route)
    resolution = resolve_task_family(
        task_type=TaskType.STATUS_QUERY,
        requested_output="report",
        goals=[{"goal_id": "goal_1_generate_report", "goal_type": "generate_report"}],
        resolved_context={},
        intent_stack=[],
    )
    route = TaskRoute(
        primary_task_type=TaskType.STATUS_QUERY,
        task_family=resolution.task_family,
        task_family_reason=resolution.reason,
        task_family_source=resolution.source,
        task_family_warnings=resolution.warnings,
        intent_stack=[],
    )
    plan = build_workflow_plan(route)

    assert resolution.task_family == "runtime_status"
    assert "task_family_goal_mismatch" in resolution.warnings
    assert "task_family_goal_mismatch" not in route.flags
    assert route.intent_stack == baseline_route.intent_stack
    assert plan.resolved_nodes == baseline.resolved_nodes
    assert plan.runtime_tools == baseline.runtime_tools


def test_public_output_task_family_values_are_stable() -> None:
    route = route_task(payload=fallback_understanding_payload("J1 当前状态", "维修员"), message="J1 当前状态")
    decision = _decision("生成 J1 的运行报告")
    direct_decision = SingleAgentDecision(
        task_family="meta",
        task_family_reason="direct_response fast path",
        task_family_source="direct_response",
    )
    direct_payload = build_direct_complete_payload(
        thread_id="thread.family",
        trace_id="trace.family",
        request_id="request.family",
        final_answer="ok",
        decision=direct_decision,
        trace=AgentTrace(
            trace_id="trace.family",
            request_id="request.family",
            thread_id="thread.family",
            user_identity="tester",
            user_message="你好",
        ),
        event_count=0,
    )
    snapshot = build_plan_snapshot(
        message="J1 当前状态怎么样",
        thread_id="thread.family.plan",
        user_identity="engineer",
        auth_context=AuthContext(user_id="engineer", role="engineer", asset_scope=["J1"], table_scope=["*"]),
    )

    values = [
        route.task_family,
        decision.task_family,
        direct_payload["task_family"],
        direct_payload["decision"]["task_family"],
        snapshot.task_family,
        snapshot.workflow_route["task_family"],
        snapshot.intent_axes["task_family"],
    ]
    assert all(value in PUBLIC_TASK_FAMILIES for value in values)
    assert not any(value in {None, "unknown", "status_query", "report_generation"} for value in values)
