from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.context import (
    ConversationDiagnosisState,
    DiagnosisCase,
    apply_context_resolution,
)
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.workflow import TaskType, route_task


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


def test_alarm_triage_without_device_blocks_realtime_subgoals() -> None:
    message = "E102 是什么意思？是不是现在设备有故障？应该如何解决？"
    payload = fallback_understanding_payload(message, "游客")
    route = route_task(payload=payload, message=message)
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )

    assert route.primary_task_type == TaskType.ALARM_TRIAGE
    assert route.task_family == "diagnosis"
    assert payload["fault_code_hint"] == "E102"
    assert decision.primary_task_type == "alarm_triage"
    assert decision.task_family == "diagnosis"
    assert decision.needs_knowledge is True
    assert decision.needs_sql is False
    assert decision.enabled_nodes["knowledge"] is True
    assert decision.enabled_nodes["workorder_decision"] is False
    assert any(
        item["type"] == "check_current_fault_status"
        and item["status"] == "blocked"
        and "device_id" in item["missing_slots"]
        for item in decision.subgoals
    )


def test_alarm_triage_with_device_enables_sql_and_workorder_decision() -> None:
    message = "pump_001 的 E102 故障码是什么意思？是不是现在设备有故障？应该如何解决？"
    payload = fallback_understanding_payload(message, "游客")
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )

    assert decision.primary_task_type == "alarm_triage"
    assert decision.objects["device_ids"] == ["pump_001"]
    assert decision.objects["alarm_codes"] == ["E102"]
    assert decision.needs_sql is True
    assert decision.enabled_nodes["workorder_decision"] is True
    assert all(item["status"] == "ready" for item in decision.subgoals if item["required"])


def test_action_request_routes_to_guarded_workflow() -> None:
    message = "帮我重启 pump_001"
    payload = fallback_understanding_payload(message, "维修员")
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )

    assert decision.primary_task_type == "action_request"
    assert decision.task_family == "action_or_workorder"
    assert decision.action_type == "restart_device"
    assert decision.risk_level == "high_risk"
    assert decision.enabled_nodes["permission_check"] is True
    assert decision.enabled_nodes["risk_check"] is True
    assert "device_control.write" in decision.workflow_policy["forbidden_tools"]
    assert "sql_db_query" in decision.runtime_tools
    assert "query_knowledge_base" in decision.runtime_tools


def test_device_running_report_collects_fresh_sql_evidence() -> None:
    message = "生成J1号机的运行报告"
    payload = fallback_understanding_payload(message, "维修员")
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )

    assert payload["equipment_hint"] == "J1"
    assert payload["needs_sql"] is True
    assert decision.primary_task_type == "report_generation"
    assert decision.task_family == "reporting"
    assert decision.needs_sql is True
    assert decision.enabled_nodes["sql"] is True
    assert "sql_db_query" in decision.runtime_tools


def test_permission_scope_question_routes_without_runtime_tools() -> None:
    message = "我这个身份可以访问到哪些设备呀？"
    payload = fallback_understanding_payload(message, "游客")
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )

    assert decision.primary_task_type == "permission_scope_query"
    assert decision.task_family == "meta"
    assert decision.needs_sql is False
    assert decision.needs_knowledge is False
    assert decision.enabled_nodes["sql"] is False
    assert decision.enabled_nodes["knowledge"] is False
    assert decision.runtime_tools == []


def test_g120_model_text_is_not_fault_code() -> None:
    message = "G120电机2最新数据情况如何？"
    payload = fallback_understanding_payload(message, "游客")
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )

    assert payload["fault_code_hint"] is None
    assert decision.objects["alarm_codes"] == []
    assert "explain_alarm_code" not in decision.intent_stack


def _state(
    *,
    asset: str | None = None,
    fault_codes: list[str] | None = None,
    evidence_bundle_id: str | None = None,
    report_url: str | None = None,
) -> ConversationDiagnosisState:
    case = DiagnosisCase(
        case_id=evidence_bundle_id or "case.test",
        thread_id="thread.test",
        active_asset=asset,
        active_fault_codes=fault_codes or [],
        last_evidence_bundle_id=evidence_bundle_id,
        last_report_url=report_url,
    )
    return ConversationDiagnosisState(thread_id="thread.test", active_case_id=case.case_id, cases=[case])


def _decision_with_state(message: str, state: ConversationDiagnosisState):
    payload = fallback_understanding_payload(message, "维修员")
    apply_context_resolution(payload=payload, message=message, state=state)
    request = _request(message, payload)
    return decide_capabilities(
        payload=payload,
        request=request,
        message=message,
        report_from_previous_artifact=False,
        conversation_state=state,
    )


def test_context_reuses_previous_j1_for_pronoun_severity_question() -> None:
    decision = _decision_with_state("它严重吗", _state(asset="J1"))

    assert decision.objects["device_ids"] == ["J1"]
    assert decision.context_resolution["used_active_asset"] is True
    assert "severity_assessment" in decision.intent_stack
    assert decision.needs_sql is True
    assert decision.needs_knowledge is True


def test_context_reuses_previous_fault_code_for_resolution_question() -> None:
    decision = _decision_with_state("怎么处理", _state(fault_codes=["A07089"]))

    assert decision.objects["alarm_codes"] == ["A07089"]
    assert decision.context_resolution["used_active_fault_codes"] is True
    assert "resolution_recommendation" in decision.intent_stack
    assert decision.needs_knowledge is True


def test_composite_alarm_question_builds_intent_stack_and_safe_union() -> None:
    message = "A07089 是什么，现在设备有故障吗，怎么解决"
    payload = fallback_understanding_payload(message, "维修员")
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
    )

    assert decision.primary_task_type == "alarm_triage"
    assert decision.task_family == "diagnosis"
    assert decision.candidate_task_types
    assert "explain_alarm_code" in decision.intent_stack
    assert "check_current_status" in decision.intent_stack
    assert "resolution_recommendation" in decision.intent_stack
    assert "explain_fault_code" in [goal["goal_type"] for goal in decision.goals]
    assert "recommend_resolution" in [goal["goal_type"] for goal in decision.goals]
    assert "explain_alarm_code" in decision.goal_set["intent_stack_projection"]
    assert decision.flags["safe_union_workflow"] is True
    assert decision.needs_knowledge is True


def test_report_handoff_uses_previous_evidence_bundle_context() -> None:
    state = _state(asset="J1", fault_codes=["A07089"], evidence_bundle_id="eb_trace")
    message = "基于刚才结果生成报告"
    payload = fallback_understanding_payload(message, "维修员")
    apply_context_resolution(payload=payload, message=message, state=state)
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=True,
        conversation_state=state,
    )

    assert decision.primary_task_type == "report_generation"
    assert decision.task_family == "reporting"
    assert decision.report_from_previous_artifact is True
    assert decision.resolved_context.get("relation_to_previous") in {None, "report_handoff"}
    assert decision.context_resolution["last_evidence_bundle_id"] == "eb_trace"
    assert decision.active_case_id == "eb_trace"
    assert "report_generation" in decision.intent_stack
    assert decision.goal_set["primary_goal_id"]
    assert "generate_report" in [goal["goal_type"] for goal in decision.goals]


def test_switch_to_j2_overrides_previous_active_asset() -> None:
    decision = _decision_with_state("换 J2 看一下", _state(asset="J1"))

    assert decision.objects["device_ids"] == ["J2"]
    assert decision.context_resolution["source"] == "current_message"
    assert decision.context_resolution["used_active_asset"] is False
    assert "check_current_status" in decision.intent_stack
    assert decision.needs_sql is True


def test_permission_question_does_not_reuse_previous_active_asset() -> None:
    state = _state(asset="G120电机2", fault_codes=["A07089"])
    payload = fallback_understanding_payload("我这个身份可以访问到哪些设备呀？", "游客")
    resolution = apply_context_resolution(payload=payload, message="我这个身份可以访问到哪些设备呀？", state=state)

    assert payload["equipment_hint"] is None
    assert payload["fault_code_hint"] is None
    assert resolution["used_active_asset"] is False
    assert resolution["active_asset"] is None


def test_pronoun_with_multiple_candidate_assets_requests_clarification() -> None:
    cases = [
        DiagnosisCase(case_id="case.j1", thread_id="thread.test", active_asset="J1"),
        DiagnosisCase(case_id="case.j2", thread_id="thread.test", active_asset="J2"),
    ]
    state = ConversationDiagnosisState(thread_id="thread.test", active_case_id="case.j1", cases=cases)
    payload = fallback_understanding_payload("它严重吗", "维修员")

    resolution = apply_context_resolution(payload=payload, message="它严重吗", state=state)

    assert payload["equipment_hint"] is None
    assert resolution["source"] == "ambiguous"
    assert resolution["unresolved_questions"]
