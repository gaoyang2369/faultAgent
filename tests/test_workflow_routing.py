from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.context import ConversationDiagnosisState, DiagnosisCase, apply_context_resolution
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.workflow import route_task


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


def _goal_types(decision) -> set[str]:
    return {goal["goal_type"] for goal in decision.goals}


def test_alarm_triage_without_device_blocks_runtime_subgoals() -> None:
    decision = _decision("E102 是什么意思？是不是现在设备有故障？应该如何解决？")

    assert decision.task_family == "diagnosis"
    assert decision.needs_knowledge is True
    assert decision.needs_sql is False
    assert {"explain_fault_code", "check_runtime_status", "recommend_resolution"} <= _goal_types(decision)
    assert any(item["status"] == "blocked" for item in decision.subgoals)


def test_alarm_triage_with_device_enables_sql_and_workorder_decision() -> None:
    decision = _decision("pump_001 的 E102 故障码是什么意思？是不是现在设备有故障？应该如何解决？")

    assert decision.objects["device_ids"] == ["pump_001"]
    assert decision.objects["alarm_codes"] == ["E102"]
    assert decision.needs_sql is True
    assert decision.enabled_nodes["workorder_decision"] is True
    assert all(item["status"] == "ready" for item in decision.subgoals if item["required"])


def test_action_request_routes_to_guarded_workflow() -> None:
    decision = _decision("帮我重启 pump_001")

    assert decision.task_family == "action_or_workorder"
    assert decision.action_type == "restart_device"
    assert decision.risk_level == "high_risk"
    assert decision.enabled_nodes["permission_check"] is True
    assert decision.enabled_nodes["risk_check"] is True
    assert "device_control.write" in decision.workflow_policy["forbidden_tools"]


def test_knowledge_only_alarm_code_does_not_plan_sql() -> None:
    route = route_task(payload=fallback_understanding_payload("A07089 是什么意思", "游客"), message="A07089 是什么意思")

    assert route.task_family == "knowledge_lookup"
    assert route.flags["need_knowledge"] is True
    assert route.flags["need_sql"] is False


def test_device_running_report_collects_fresh_sql_evidence() -> None:
    decision = _decision("生成J1号机的运行报告")

    assert decision.task_family == "reporting"
    assert decision.needs_sql is True
    assert decision.enabled_nodes["sql"] is True
    assert "sql_db_query" in decision.runtime_tools
    assert "generate_report" in _goal_types(decision)


def test_permission_scope_question_routes_without_runtime_tools() -> None:
    decision = _decision("我这个身份可以访问到哪些设备呀？")

    assert decision.task_family == "meta"
    assert decision.needs_sql is False
    assert decision.needs_knowledge is False
    assert decision.runtime_tools == []


def test_g120_model_text_is_not_fault_code() -> None:
    decision = _decision("G120电机2最新数据情况如何？")

    assert decision.objects["alarm_codes"] == []
    assert "explain_fault_code" not in _goal_types(decision)


def _state(asset: str | None = None, fault_codes: list[str] | None = None) -> ConversationDiagnosisState:
    case = DiagnosisCase(
        case_id="case.test",
        thread_id="thread.test",
        active_asset=asset,
        active_fault_codes=fault_codes or [],
    )
    return ConversationDiagnosisState(thread_id="thread.test", active_case_id=case.case_id, cases=[case])


def test_context_reuses_previous_j1_for_pronoun_severity_question() -> None:
    payload = fallback_understanding_payload("它严重吗", "维修员")
    state = _state(asset="J1")
    apply_context_resolution(payload=payload, message="它严重吗", state=state)
    decision = decide_capabilities(payload=payload, request=_request("它严重吗", payload), message="它严重吗", report_from_previous_artifact=False, conversation_state=state)

    assert decision.objects["device_ids"] == ["J1"]
    assert "assess_severity" in _goal_types(decision)
    assert decision.needs_sql is True


def test_switch_to_j2_overrides_previous_active_asset() -> None:
    payload = fallback_understanding_payload("换 J2 看一下", "维修员")
    state = _state(asset="J1")
    apply_context_resolution(payload=payload, message="换 J2 看一下", state=state)
    decision = decide_capabilities(payload=payload, request=_request("换 J2 看一下", payload), message="换 J2 看一下", report_from_previous_artifact=False, conversation_state=state)

    assert decision.objects["device_ids"] == ["J2"]
    assert decision.context_resolution["source"] == "current_message"
    assert "check_runtime_status" in _goal_types(decision)
