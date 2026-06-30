from __future__ import annotations

from fault_diagnosis.context import (
    CASE_STATE_SNAPSHOT_VERSION,
    ArtifactBackedCaseStore,
    ContextManager,
    case_state_from_artifact,
)
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.security.permissions import build_auth_context
from fault_diagnosis.single_agent.intent import fallback_understanding_payload


def _artifact(
    *,
    thread_id: str = "thread.context",
    asset: str = "J1",
    fault_code: str = "A07089",
    stale: bool = False,
    snapshot: dict | None = None,
) -> DiagnosisArtifactEnvelope:
    freshness_label = "已滞后" if stale else "当前"
    payload = {
        "request": {
            "user_message": f"生成 {asset} 运行报告",
            "user_identity": "管理员",
            "equipment_hint": asset,
            "fault_code_hint": fault_code,
            "analysis_goal": "运行报告",
        },
        "decision": {
            "objects": {"device_ids": [asset], "alarm_codes": [fault_code]},
            "context_resolution": {"active_asset": asset, "active_fault_codes": [fault_code]},
            "time_window": {"default_strategy": "最近"},
        },
        "evidence_bundle": {"bundle_id": f"eb_{asset}", "trace_id": "trace.previous"},
        "report_artifact": {
            "success": True,
            "report_filename": f"{asset}.html",
            "report_url": f"/reports/{asset}.html",
            "save_result": f"/reports/{asset}.html",
        },
        "analysis_artifact": {
            "success": True,
            "conclusion": f"{asset} {fault_code} 持续出现",
            "basis": ["上一轮报告显示告警"],
            "recommendations": ["刷新当前状态后确认是否派发"],
            "confidence": "medium",
        },
        "operation_report_payload": {
            "asset": asset,
            "status_level": "告警 / 需确认",
            "current_event": f"{fault_code} 持续出现",
            "data_freshness_label": freshness_label,
            "data_currentness_label": "STALE / 不代表实时状态" if stale else "CURRENT",
            "latest_sample_time": "2026-01-14 18:27:24",
            "evidence_summary": [f"{fault_code} 持续出现"],
            "next_action": "刷新当前状态后确认是否派发",
        },
        "workorder_decision": {
            "need_workorder": True,
            "status": "待确认",
            "reason": "上一轮诊断建议生成待确认工单草稿。",
        },
    }
    if snapshot is not None:
        payload["case_state_snapshot"] = snapshot
    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
        thread_id=thread_id,
        created_at="2026-06-24T10:00:00",
        request_summary=f"生成 {asset} 运行报告",
        final_answer=f"上一轮报告：{fault_code} 持续出现。",
        report_filename=f"{asset}.html",
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


def test_report_artifact_workorder_followup_resolves_action_context() -> None:
    manager = _manager_with_artifact(_artifact())
    payload = fallback_understanding_payload("从结果来看貌似有故障呀？是不是要生成工单？", "维修员")

    resolved = manager.resolve(
        thread_id="thread.context",
        message="从结果来看貌似有故障呀？是不是要生成工单？",
        auth_context=_engineer(),
        current_payload=payload,
    )

    assert resolved.relation_to_previous == "action_followup"
    assert resolved.referenced_artifact_id == "eb_J1"
    assert resolved.inherited_slots["device"] == "J1"
    assert resolved.pending_actions
    assert payload["equipment_hint"] == "J1"


def test_previous_result_report_handoff_resolves_report_context() -> None:
    manager = _manager_with_artifact(_artifact())
    payload = fallback_understanding_payload("基于刚才结果导出报告", "维修员")

    resolved = manager.resolve(
        thread_id="thread.context",
        message="基于刚才结果导出报告",
        auth_context=_engineer(),
        current_payload=payload,
    )

    assert resolved.relation_to_previous == "report_handoff"
    assert resolved.referenced_report_id == "/reports/J1.html"
    assert resolved.inherited_slots["evidence_bundle"] == "eb_J1"


def test_explicit_new_device_does_not_inherit_previous_j1() -> None:
    manager = _manager_with_artifact(_artifact(asset="J1"))
    payload = fallback_understanding_payload("换 J2 看一下", "维修员")

    resolved = manager.resolve(
        thread_id="thread.context",
        message="换 J2 看一下",
        auth_context=_engineer(asset_scope=["J1", "J2"]),
        current_payload=payload,
    )

    assert resolved.relation_to_previous in {"new_case", "correction"}
    assert resolved.inherited_slots == {}
    assert payload["equipment_hint"] == "J2"


def test_ambiguous_pronoun_with_multiple_cases_requests_context() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    save_thread_artifact(_artifact(asset="J1"))
    save_thread_artifact(_artifact(asset="J2"))
    payload = fallback_understanding_payload("它严重吗", "维修员")

    resolved = ContextManager().resolve(
        thread_id="thread.context",
        message="它严重吗",
        auth_context=_engineer(asset_scope=["J1", "J2"]),
        current_payload=payload,
    )

    assert resolved.relation_to_previous == "ambiguous"
    assert resolved.missing_context
    assert "J1" in resolved.candidates["assets"]
    assert "J2" in resolved.candidates["assets"]


def test_unauthorized_context_does_not_inherit_artifact_slots() -> None:
    manager = _manager_with_artifact(_artifact(asset="J1"))
    payload = fallback_understanding_payload("它要不要生成工单？", "维修员")

    resolved = manager.resolve(
        thread_id="thread.context",
        message="它要不要生成工单？",
        auth_context=_engineer(asset_scope=["J2"]),
        current_payload=payload,
    )

    assert resolved.inherited_slots == {}
    assert resolved.pending_actions == []
    assert resolved.missing_context
    assert "J1" not in " ".join(resolved.missing_context)
    assert "reports" not in str(resolved.model_dump())
    assert payload.get("equipment_hint") is None
    assert payload.get("fault_code_hint") is None


def test_bad_snapshot_falls_back_to_artifact_payload_projection() -> None:
    envelope = _artifact(
        snapshot={
            "schema_version": CASE_STATE_SNAPSHOT_VERSION,
            "thread_id": "wrong.thread",
            "case_id": "bad",
            "active_asset": "BAD",
        }
    )

    case = case_state_from_artifact(envelope)

    assert case is not None
    assert case.active_asset == "J1"
    assert case.latest_evidence_bundle_id == "eb_J1"
    assert case.projection_warnings


def test_bad_snapshot_fallback_reason_surfaces_in_resolved_context() -> None:
    manager = _manager_with_artifact(
        _artifact(
            snapshot={
                "schema_version": "case_state_snapshot.v0",
                "thread_id": "thread.context",
                "case_id": "bad",
                "active_asset": "BAD",
            }
        )
    )
    payload = fallback_understanding_payload("基于刚才结果导出报告", "维修员")

    resolved = manager.resolve(
        thread_id="thread.context",
        message="基于刚才结果导出报告",
        auth_context=_engineer(),
        current_payload=payload,
    )

    assert resolved.relation_to_previous == "report_handoff"
    assert resolved.inherited_slots["device"] == "J1"
    assert "schema_version" in resolved.context_resolution_reason
    assert "回退投影" in resolved.context_resolution_reason
