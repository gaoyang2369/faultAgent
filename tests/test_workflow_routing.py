from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
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
    assert payload["fault_code_hint"] == "E102"
    assert decision.primary_task_type == "alarm_triage"
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
    assert decision.needs_sql is True
    assert decision.enabled_nodes["sql"] is True
    assert "sql_db_query" in decision.runtime_tools
