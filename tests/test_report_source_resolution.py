from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fault_diagnosis.agent_runtime.sse_adapter import parse_sse_chunk
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import (
    clear_all_artifacts,
    configure_artifact_store_backend,
    save_thread_artifact,
)
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.security.permissions import build_auth_context
from fault_diagnosis.single_agent.planner import build_plan_snapshot
from fault_diagnosis.single_agent.reporting.source_resolution import (
    build_report_readiness,
    resolve_report_source,
)
from fault_diagnosis.single_agent.runner import RestrictedSingleAgentRunner


def _auth():
    return build_auth_context(
        user_id="engineer",
        role="engineer",
        asset_scope=["J1", "J2"],
        table_scope=["real_data_01"],
    )


def _artifact(
    *,
    thread_id: str = "thread.report",
    created_at: str = "2026-07-03T10:00:00",
    device: str = "J1",
    reportable: bool = True,
    with_material: bool = True,
) -> DiagnosisArtifactEnvelope:
    payload = {
        "runtime": "restricted_single_agent",
        "task_family": "runtime_status",
        "reportable": reportable,
        "request": {"equipment_hint": device, "analysis_goal": f"{device} 最近状态"},
        "decision": {
            "task_family": "runtime_status",
            "objects": {"device_ids": [device], "alarm_codes": ["A07089"]},
            "context_resolution": {"active_asset": device, "active_fault_codes": ["A07089"]},
        },
        "sql_artifact": {
            "success": True,
            "row_count": 1 if with_material else 0,
            "source_table": "real_data_01",
            "raw_output": "[{\"device_name\":\"J1\",\"fault_code\":\"A07089\",\"create_time\":\"2026-07-03 09:55:00\"}]"
            if with_material
            else "",
        },
        "analysis_artifact": {"success": True, "conclusion": "J1 最近有 A07089 事件。"},
        "evidence_bundle": {"bundle_id": f"eb_{created_at}", "evidence_items": [{"evidence_id": "ev_sql"}]}
        if with_material
        else {"bundle_id": f"eb_{created_at}", "evidence_items": []},
    }
    if with_material:
        payload["normalized_rows"] = [
            {"device_name": device, "fault_code": "A07089", "create_time": "2026-07-03 09:55:00"}
        ]
    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.STATUS_QUERY,
        thread_id=thread_id,
        created_at=created_at,
        request_summary=f"{device} 最近状态",
        final_answer="状态摘要",
        payload=payload,
        evidence=[],
    )


def setup_function() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    clear_all_artifacts()


def test_status_query_reportable_material_can_reuse_artifact() -> None:
    save_thread_artifact(_artifact())

    decision = resolve_report_source(
        thread_id="thread.report",
        message="那就导出报告吧",
        auth_context=_auth(),
        current_payload={"needs_report": True, "equipment_hint": "J1"},
        resolved_context={"relation_to_previous": "report_handoff", "active_asset": "J1"},
    )

    assert decision.mode == "reuse_artifact"
    assert decision.readiness["passed"] is True
    assert decision.referenced_artifact_id == "2026-07-03T10:00:00"


def test_status_query_without_rebuild_material_refreshes_sql() -> None:
    save_thread_artifact(_artifact(reportable=True, with_material=False))

    decision = resolve_report_source(
        thread_id="thread.report",
        message="那就导出报告吧",
        auth_context=_auth(),
        current_payload={"needs_report": True, "equipment_hint": "J1"},
        resolved_context={"relation_to_previous": "report_handoff", "active_asset": "J1"},
    )

    assert decision.mode == "refresh_sql"
    assert "artifact_missing_reportable_material" in decision.blockers


def test_multiple_reportable_candidates_choose_latest_valid_deterministically() -> None:
    save_thread_artifact(_artifact(created_at="2026-07-03T10:00:00", device="J1"))
    save_thread_artifact(_artifact(created_at="2026-07-03T10:01:00", device="J1"))

    decision = resolve_report_source(
        thread_id="thread.report",
        message="那就导出报告吧",
        auth_context=_auth(),
        current_payload={"needs_report": True, "equipment_hint": "J1"},
        resolved_context={"relation_to_previous": "report_handoff", "active_asset": "J1"},
    )

    assert decision.mode == "reuse_artifact"
    assert decision.referenced_artifact_id == "2026-07-03T10:01:00"


def test_readiness_fail_does_not_call_save_report(monkeypatch) -> None:
    called = False

    def fake_get_report_tool():
        nonlocal called
        called = True
        raise AssertionError("save_report should not be requested")

    monkeypatch.setattr("fault_diagnosis.single_agent.stages.get_report_tool", fake_get_report_tool)

    runner = RestrictedSingleAgentRunner(
        message="导出报告",
        thread_id="thread.empty",
        user_identity="维修员",
        request_id="req-report-empty",
        stream_id="stream-report-empty",
        trace_id="trace-report-empty",
        auth_context=_auth(),
    )

    async def collect():
        return [
            item
            async for item in runner.stream_events(SimpleNamespace(state=SimpleNamespace(chat_model=None)))
        ]

    chunks = asyncio.run(collect())
    complete = [parse_sse_chunk(item) for item in chunks if item.startswith("event: complete")][-1]

    assert called is False
    assert complete is not None
    assert complete[1]["decision"]["report_source_mode"] == "blocked_missing_context"
    assert complete[1]["ui_payload"]["report_generated"] is False


def test_plan_and_stream_share_report_source_mode_for_same_input(monkeypatch) -> None:
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.get_report_tool", lambda: (_ for _ in ()).throw(AssertionError("no report")))
    auth = _auth()
    plan = build_plan_snapshot(
        message="导出报告",
        thread_id="thread.empty",
        user_identity="维修员",
        auth_context=auth,
    )
    runner = RestrictedSingleAgentRunner(
        message="导出报告",
        thread_id="thread.empty",
        user_identity="维修员",
        request_id="req-report-plan-stream",
        stream_id="stream-report-plan-stream",
        trace_id="trace-report-plan-stream",
        auth_context=auth,
    )

    async def collect():
        return [
            item
            async for item in runner.stream_events(SimpleNamespace(state=SimpleNamespace(chat_model=None)))
        ]

    chunks = asyncio.run(collect())
    complete = [parse_sse_chunk(item) for item in chunks if item.startswith("event: complete")][-1]

    assert complete is not None
    assert plan.report_source_mode == complete[1]["decision"]["report_source_mode"]
    assert plan.report_source_mode == "blocked_missing_context"
