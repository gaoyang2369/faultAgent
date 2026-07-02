from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.workflow import PUBLIC_TASK_FAMILIES, build_workflow_plan, resolve_task_family, route_task


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


def _decision(message: str):
    payload = fallback_understanding_payload(message, "维修员")
    return decide_capabilities(payload=payload, request=_request(message, payload), message=message, report_from_previous_artifact=False)


def test_task_family_resolves_from_goal_axes() -> None:
    cases = {
        "A07089 是什么意思": "knowledge_lookup",
        "J1 当前运行状态怎么样": "runtime_status",
        "J1 的 A07089 现在还在报警吗，怎么处理": "diagnosis",
        "生成 J1 的运行报告": "reporting",
        "帮我重启 J1": "action_or_workorder",
        "我这个身份可以访问到哪些设备呀？": "meta",
    }

    for message, task_family in cases.items():
        decision = _decision(message)
        assert decision.task_family == task_family
        assert decision.task_family in PUBLIC_TASK_FAMILIES


def test_resolve_task_family_prefers_report_and_action_axes() -> None:
    assert resolve_task_family(requested_output="report", goals=[]).task_family == "reporting"
    assert resolve_task_family(action_target="workorder", goals=[]).task_family == "action_or_workorder"
    assert resolve_task_family(goals=[{"goal_type": "answer_meta_question"}]).task_family == "meta"


def test_policy_id_tracks_goal_native_task_family() -> None:
    route = route_task(payload=fallback_understanding_payload("J1 当前状态", "维修员"), message="J1 当前状态")
    plan = build_workflow_plan(route)

    assert route.task_family == "runtime_status"
    assert plan.policy.policy_id == "status_query_v1"
