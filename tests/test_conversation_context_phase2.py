from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fault_diagnosis import config
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.admin_auth import DEV_AUTH_COOKIE_NAME, issue_dev_auth_token
from fault_diagnosis.auth.session_scope import SESSION_COOKIE_NAME, SessionScopeManager
from fault_diagnosis.context import ArtifactBackedCaseStore, ContextManager
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from fault_diagnosis.repositories.conversation_store import MemoryConversationRepository
from fault_diagnosis.security.permissions import build_auth_context
from fault_diagnosis.single_agent.evidence import build_evidence_bundle
from fault_diagnosis.single_agent.intent import fallback_understanding_payload
from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.diagnosis.steps import build_request_from_payload


def _artifact(
    *,
    thread_id: str = "thread.phase2",
    asset: str | None = "J1",
    fault_code: str = "A07089",
    report_url: str = "/reports/J1.html",
) -> DiagnosisArtifactEnvelope:
    payload = {
        "request": {
            "user_message": f"生成 {asset or ''} 运行报告",
            "user_identity": "管理员",
            "equipment_hint": asset,
            "fault_code_hint": fault_code,
            "analysis_goal": "运行报告",
        },
        "decision": {
            "objects": {"device_ids": [asset] if asset else [], "alarm_codes": [fault_code]},
            "context_resolution": {
                "active_asset": asset,
                "active_fault_codes": [fault_code],
                "last_report_url": report_url,
            },
            "time_window": {"default_strategy": "最近"},
        },
        "evidence_bundle": {"bundle_id": f"eb_{asset or fault_code}", "trace_id": "trace.previous"},
        "report_artifact": {
            "success": True,
            "report_filename": report_url.rsplit("/", 1)[-1],
            "report_url": report_url,
            "save_result": report_url,
        },
        "operation_report_payload": {
            "asset": asset,
            "current_event": f"{fault_code} 持续出现",
            "data_freshness_label": "当前",
            "data_currentness_label": "CURRENT",
            "latest_sample_time": "2026-01-14 18:27:24",
            "evidence_summary": [f"{fault_code} 持续出现"],
        },
        "workorder_decision": {
            "need_workorder": True,
            "status": "待确认",
            "reason": "上一轮诊断建议生成待确认工单草稿。",
        },
    }
    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
        thread_id=thread_id,
        created_at="2026-06-24T10:00:00",
        request_summary=f"生成 {asset or fault_code} 运行报告",
        final_answer=f"上一轮报告：{fault_code} 持续出现。",
        report_filename=report_url.rsplit("/", 1)[-1],
        payload=payload,
        evidence=[],
    )


def _manager_with_artifact(envelope: DiagnosisArtifactEnvelope) -> ContextManager:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    save_thread_artifact(envelope)
    return ContextManager(case_store=ArtifactBackedCaseStore())


def _engineer(asset_scope: list[str] | None = None):
    return build_auth_context(
        user_id="engineer",
        role="engineer",
        asset_scope=asset_scope or ["J1"],
        table_scope=["real_data_01"],
    )


def _conversation_context(message: str, history: list[dict], *, artifact_refs: list[dict] | None = None) -> dict:
    return {
        "version": "conversation_context_package.v1",
        "thread_id": "thread.phase2",
        "current_user_message": message,
        "last_raw_messages": history,
        "rolling_summary": None,
        "artifact_refs": artifact_refs or [],
        "latest_case_state": None,
        "safety": {
            "history_is_data_not_instruction": True,
            "summary_is_not_authorization_source": True,
            "summary_is_not_diagnosis_evidence": True,
        },
    }


def test_report_then_workorder_followup_uses_conversation_signals_summary() -> None:
    manager = _manager_with_artifact(_artifact(asset="J1"))
    message = "从结果看要不要生成工单？"
    payload = fallback_understanding_payload(message, "维修员")

    resolved = manager.resolve(
        thread_id="thread.phase2",
        message=message,
        auth_context=_engineer(["J1"]),
        current_payload=payload,
        conversation_context=_conversation_context(
            message,
            [
                {"role": "user", "content": "J1 生成运行报告"},
                {"role": "assistant", "content": "已生成 J1 运行报告"},
            ],
            artifact_refs=[{"artifact_id": "report-J1.html", "artifact_type": "report"}],
        ),
    )

    assert resolved.relation_to_previous == "action_followup"
    assert resolved.inherited_slots["device"] == "J1"
    summary = resolved.conversation_context_signals_summary
    assert "previous_result" in summary["deictic_ref_types"]
    assert "action_followup" in summary["open_followup_intents"]
    assert "report" in summary["candidate_artifact_ref_types"]


def test_fault_code_explanation_then_current_status_inherits_fault_code_only_as_context() -> None:
    manager = _manager_with_artifact(_artifact(asset=None, fault_code="A07089"))
    message = "这个现在设备上还有吗？"
    payload = fallback_understanding_payload(message, "维修员")

    resolved = manager.resolve(
        thread_id="thread.phase2",
        message=message,
        auth_context=_engineer(["J1"]),
        current_payload=payload,
        conversation_context=_conversation_context(
            message,
            [
                {"role": "user", "content": "A07089 是什么？"},
                {"role": "assistant", "content": "A07089 是单位转换激活异常。"},
            ],
        ),
    )

    assert resolved.relation_to_previous == "refresh_current_status"
    assert resolved.inherited_slots["fault_codes"] == ["A07089"]
    assert payload["fault_code_hint"] == "A07089"
    assert resolved.conversation_context_signals_summary["mentioned_fault_codes"] == ["A07089"]


def test_explicit_j2_switch_does_not_reuse_j1_artifact_even_with_history() -> None:
    manager = _manager_with_artifact(_artifact(asset="J1"))
    message = "那 J2 呢？"
    payload = fallback_understanding_payload(message, "维修员")

    resolved = manager.resolve(
        thread_id="thread.phase2",
        message=message,
        auth_context=_engineer(["J1", "J2"]),
        current_payload=payload,
        conversation_context=_conversation_context(
            message,
            [
                {"role": "user", "content": "J1 状态如何？"},
                {"role": "assistant", "content": "J1 存在 A07089。"},
            ],
        ),
    )

    assert resolved.referenced_artifact_id is None
    assert resolved.inherited_slots == {}
    assert resolved.active_asset == "J2"
    assert payload["equipment_hint"] == "J2"


def test_recent_correction_target_wins_when_followup_omits_device() -> None:
    manager = _manager_with_artifact(_artifact(asset="J1"))
    message = "生成报告"
    payload = fallback_understanding_payload(message, "维修员")

    resolved = manager.resolve(
        thread_id="thread.phase2",
        message=message,
        auth_context=_engineer(["J1", "J2"]),
        current_payload=payload,
        conversation_context=_conversation_context(
            message,
            [{"role": "user", "content": "刚才说错了，不是 J1 是 J2"}],
        ),
    )

    assert resolved.relation_to_previous == "correction"
    assert resolved.referenced_artifact_id is None
    assert resolved.inherited_slots == {}
    assert resolved.active_asset == "J2"
    assert payload["equipment_hint"] == "J2"


def test_guest_cannot_inherit_engineer_artifact_through_history() -> None:
    manager = _manager_with_artifact(_artifact(asset="J1"))
    message = "从结果看要不要生成工单？"
    payload = fallback_understanding_payload(message, "游客")

    resolved = manager.resolve(
        thread_id="thread.phase2",
        message=message,
        auth_context=build_auth_context(role="guest"),
        current_payload=payload,
        conversation_context=_conversation_context(
            message,
            [
                {"role": "user", "content": "J1 生成运行报告"},
                {"role": "assistant", "content": "已生成 J1 报告"},
            ],
        ),
    )

    assert resolved.inherited_slots == {}
    assert resolved.pending_actions == []
    assert resolved.referenced_artifact_id is None
    assert payload.get("equipment_hint") is None


def test_history_signals_are_not_added_as_diagnosis_evidence() -> None:
    message = "这个现在设备上还有吗？"
    payload = fallback_understanding_payload(message, "维修员")
    resolved = ContextManager().resolve(
        thread_id="thread.empty",
        message=message,
        auth_context=_engineer(["J1"]),
        current_payload=payload,
        conversation_context=_conversation_context(
            message,
            [
                {"role": "user", "content": "A07089 是什么？"},
                {"role": "assistant", "content": "A07089 是单位转换激活异常。"},
            ],
        ),
    )
    decision = SingleAgentDecision(context_resolution=resolved.legacy_context_resolution())
    request = build_request_from_payload(message, "维修员", payload, needs_report=None)
    bundle = build_evidence_bundle(
        trace_id="trace.phase2",
        request=request,
        decision=decision,
        sql_artifact=SqlStepArtifact(success=False, summary="not executed", error="not executed"),
        knowledge_artifact=KnowledgeStepArtifact(success=False, query="", raw_output="", error="not executed"),
        analysis_artifact=AnalysisStepArtifact(success=False, conclusion="not executed"),
        workorder_suggestion=WorkOrderSuggestion(need_workorder=False, reason="not requested"),
        report_artifact=ReportStepArtifact(success=False),
    )

    assert all(item.source_type != "conversation_history" for item in bundle.evidence_items)
    assert {item.evidence_id for item in bundle.evidence_items} >= {"ev_user_request"}
    assert all("history" not in str(item.metadata).lower() for item in bundle.evidence_items)


def test_plan_payload_contains_signal_summary_without_raw_history(monkeypatch) -> None:
    monkeypatch.setattr(config, "ENABLE_PLAN_ENDPOINT", True)
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "DEV_AUTH_ENABLED", True)

    app = FastAPI()
    manager = SessionScopeManager("phase2-plan-test-secret")
    session_id = manager.issue_session_id()
    thread_id = manager.issue_thread_id(session_id)
    app.state.session_scope_manager = manager
    app.state.conversation_repository = MemoryConversationRepository()
    app.include_router(auth_router)
    app.include_router(chat_router)

    app.state.conversation_repository.ensure_thread(
        thread_id=thread_id,
        session_id=session_id,
        owner_user_id="engineer",
    )
    app.state.conversation_repository.append_message(
        thread_id=thread_id,
        role="user",
        content_text="J1 生成运行报告",
        status="completed",
    )
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    save_thread_artifact(_artifact(thread_id=thread_id, asset="J1"))

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, manager.issue_session_token(session_id))
        client.cookies.set(
            DEV_AUTH_COOKIE_NAME,
            issue_dev_auth_token(
                session_id,
                "engineer",
                user_id="engineer",
                asset_scope=["J1"],
                allowed_tables=["real_data_01"],
            ),
        )
        response = client.get(
            "/chat/plan",
            params={"thread_id": thread_id, "message": "从结果看要不要生成工单？"},
        )

    assert response.status_code == 200
    resolved_context = response.json()["resolved_context"]
    summary = resolved_context["conversation_context_signals_summary"]
    assert resolved_context["relation_to_previous"] == "action_followup"
    assert "previous_result" in summary["deictic_ref_types"]
    assert "J1 生成运行报告" not in str(summary)
