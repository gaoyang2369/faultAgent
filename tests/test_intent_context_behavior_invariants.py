from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.context import ConversationDiagnosisState, DiagnosisCase, apply_context_resolution
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload


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


def _state(
    *,
    asset: str | None = None,
    fault_codes: list[str] | None = None,
    evidence_bundle_id: str | None = None,
) -> ConversationDiagnosisState:
    case = DiagnosisCase(
        case_id=evidence_bundle_id or "case.test",
        thread_id="thread.test",
        active_asset=asset,
        active_fault_codes=fault_codes or [],
        last_evidence_bundle_id=evidence_bundle_id,
    )
    return ConversationDiagnosisState(thread_id="thread.test", active_case_id=case.case_id, cases=[case])


def _decision(message: str, state: ConversationDiagnosisState | None = None, *, report_from_previous_artifact: bool = False):
    payload = fallback_understanding_payload(message, "维修员")
    if state is not None:
        apply_context_resolution(payload=payload, message=message, state=state)
    return decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=report_from_previous_artifact,
        conversation_state=state,
    )


def _goal_types(decision) -> set[str]:
    return {goal["goal_type"] for goal in decision.goals}


def test_report_handoff_uses_previous_evidence_bundle_context() -> None:
    decision = _decision(
        "基于刚才结果生成报告",
        _state(asset="J1", fault_codes=["A07089"], evidence_bundle_id="eb_trace"),
        report_from_previous_artifact=True,
    )

    assert decision.task_family == "reporting"
    assert decision.report_from_previous_artifact is True
    assert decision.context_resolution["last_evidence_bundle_id"] == "eb_trace"
    assert decision.active_case_id == "eb_trace"
    assert "generate_report" in _goal_types(decision)


def test_switch_to_j2_overrides_previous_active_asset() -> None:
    decision = _decision("换 J2 看一下", _state(asset="J1"))

    assert decision.objects["device_ids"] == ["J2"]
    assert decision.context_resolution["source"] == "current_message"
    assert decision.context_resolution["used_active_asset"] is False
    assert "check_runtime_status" in _goal_types(decision)


def test_permission_question_does_not_reuse_previous_active_asset() -> None:
    state = _state(asset="G120电机2", fault_codes=["A07089"])
    payload = fallback_understanding_payload("我这个身份可以访问到哪些设备呀？", "游客")
    resolution = apply_context_resolution(payload=payload, message="我这个身份可以访问到哪些设备呀？", state=state)

    assert payload["equipment_hint"] is None
    assert payload["fault_code_hint"] is None
    assert resolution["used_active_asset"] is False
    assert resolution["active_asset"] is None


def test_restart_request_is_guarded_action_family() -> None:
    decision = _decision("帮我重启设备")

    assert decision.task_family == "action_or_workorder"
    assert decision.risk_level == "high_risk"
    assert "device_control.write" in decision.workflow_policy["forbidden_tools"]
