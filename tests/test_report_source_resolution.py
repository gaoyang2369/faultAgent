from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fault_diagnosis import config
from fault_diagnosis.agent_runtime.sse_adapter import encode_sse_event
from fault_diagnosis.agent_runtime.sse_adapter import parse_sse_chunk
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import (
    clear_all_artifacts,
    configure_artifact_store_backend,
    list_thread_artifacts,
    save_thread_artifact,
)
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.repositories.conversation_store import MemoryConversationRepository
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
    root_artifact_id: str | None = None,
) -> DiagnosisArtifactEnvelope:
    payload = {
        "runtime": "restricted_single_agent",
        "task_family": "runtime_status",
        "reportable": reportable,
        **({"root_artifact_id": root_artifact_id} if root_artifact_id else {}),
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
    assert decision.selected_artifact_id == "2026-07-03T10:01:00"
    assert len(decision.candidate_summary) == 2
    assert [item["reason"] for item in decision.candidate_summary].count("selected") == 1


def test_same_root_duplicate_candidates_are_deduped_not_ambiguous() -> None:
    save_thread_artifact(_artifact(created_at="2026-07-03T10:00:00", device="J1"))
    save_thread_artifact(_artifact(created_at="2026-07-03T10:00:00", device="J1"))

    decision = resolve_report_source(
        thread_id="thread.report",
        message="那生成报告吧",
        auth_context=_auth(),
        current_payload={"needs_report": True, "equipment_hint": "J1"},
        resolved_context={"relation_to_previous": "report_handoff", "active_asset": "J1"},
    )

    assert decision.mode == "reuse_artifact"
    assert decision.referenced_artifact_id == "2026-07-03T10:00:00"
    assert decision.candidate_summary[0]["merged_candidate_count"] == 2


def test_same_priority_different_root_candidates_are_ambiguous() -> None:
    save_thread_artifact(_artifact(created_at="2026-07-03T10:00:00", device="J1", root_artifact_id="root_a"))
    save_thread_artifact(_artifact(created_at="2026-07-03T10:00:00", device="J1", root_artifact_id="root_b"))

    decision = resolve_report_source(
        thread_id="thread.report",
        message="那生成报告吧",
        auth_context=_auth(),
        current_payload={"needs_report": True, "equipment_hint": "J1"},
        resolved_context={"relation_to_previous": "report_handoff", "active_asset": "J1"},
    )

    assert decision.mode == "ambiguous"
    assert "ambiguous_report_source" in decision.blockers
    assert any(item["reason"] == "ambiguous_same_priority" for item in decision.candidate_summary)


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


def test_plan_exposes_report_handoff_candidate_summary() -> None:
    save_thread_artifact(_artifact(created_at="2026-07-03T10:00:00", device="J1"))
    save_thread_artifact(_artifact(created_at="2026-07-03T10:01:00", device="J1"))

    plan = build_plan_snapshot(
        message="那就导出报告吧",
        thread_id="thread.report",
        user_identity="维修员",
        auth_context=_auth(),
    )

    assert plan.report_source_mode == "reuse_artifact"
    assert plan.selected_artifact_id == "2026-07-03T10:01:00"
    assert plan.selected_artifact_type == DiagnosisArtifactType.STATUS_QUERY.value
    assert plan.report_candidate_artifact_count == 2
    assert len(plan.report_candidate_summary) == 2
    assert plan.report_candidate_summary[0]["created_before_current_turn"] is True


def test_chat_stream_report_handoff_reuses_previous_runtime_status_artifact(monkeypatch) -> None:
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "DEV_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "ENABLE_PLAN_ENDPOINT", True)
    save_report_inputs: list[dict[str, Any]] = []

    async def fake_json_model(self, prompt: str) -> dict[str, Any]:  # noqa: ANN001, ARG001
        return {
            "conclusion": "G120电机1 最近 50 条 real_data_01 样本中出现 A07089，负载率和温度需持续关注。",
            "basis": ["SQL 返回 50 条 real_data_01 运行样本。", "样本设备为 G120电机1，故障码 A07089。"],
            "probable_causes": ["负载波动或速度闭环偏差"],
            "verification_items": ["核对 G120电机1 最近数据窗口"],
            "recommendations": ["保持监测并复核负载率、温度和变频器状态字。"],
            "missing_information": [],
            "confidence": "medium",
        }

    async def fake_invoke_tool(self, *, tool_name: str, tool: Any, tool_input: Any, stage: str):  # noqa: ANN001, ARG001
        run_id, started_at, start_payload = self._start_tool_call(
            tool_name=tool_name,
            tool_input=tool_input,
            stage=stage,
        )
        yield encode_sse_event("tool_start", start_payload, trace_id=self.trace_id)
        if tool_name == "sql_db_query_checker":
            result = tool_input.get("query", "") if isinstance(tool_input, dict) else ""
        elif tool_name == "sql_db_query":
            result = _j1_runtime_rows(50)
        elif tool_name == "query_knowledge_base":
            result = "故障码 A07089：可能与速度偏差、负载异常或参数状态有关。"
        elif tool_name == "save_report":
            save_report_inputs.append(tool_input)
            result = "报告已保存至：/reports/j1_handoff_report.html"
        else:
            result = {"mocked": tool_name}
        self._last_step_result = result
        end_payload = self._finish_tool_call(
            tool_name=tool_name,
            run_id=run_id,
            started_at=started_at,
            stage=stage,
            output=result,
        )
        yield encode_sse_event("tool_end", end_payload, trace_id=self.trace_id)

    monkeypatch.setattr(RestrictedSingleAgentRunner, "_invoke_json_model", fake_json_model)
    monkeypatch.setattr(RestrictedSingleAgentRunner, "_invoke_restricted_tool", fake_invoke_tool)

    app = FastAPI()
    app.state.dev_mode = False
    app.state.session_scope_manager = SessionScopeManager("report-handoff-stream-test-secret")
    app.state.conversation_repository = MemoryConversationRepository()
    app.include_router(auth_router)
    app.include_router(chat_router)

    thread_id = "thread.stream.report-handoff"
    with TestClient(app) as client:
        login = client.post(
            "/auth/dev-login",
            json={"role": "engineer", "asset_scope": ["J1", "G120电机1"], "allowed_tables": ["real_data_01"]},
        )
        assert login.status_code == 200

        first_response = client.get(
            "/chat/stream",
            params={"message": "J1号电机最近数据反馈的状态怎么样？", "thread_id": thread_id},
        )
        assert first_response.status_code == 200
        first_complete = _complete_event(first_response.text)
        assert first_complete["type"] == "chat_complete"

        artifacts = list_thread_artifacts(thread_id)
        assert artifacts
        runtime_artifact = artifacts[0]
        assert runtime_artifact.workflow_type == DiagnosisArtifactType.STATUS_QUERY
        assert runtime_artifact.payload["reportable"] is True
        assert runtime_artifact.payload["row_count"] == 50
        assert runtime_artifact.payload["source_table"] == "real_data_01"
        assert "G120电机1" in runtime_artifact.payload["device_aliases"]
        assert runtime_artifact.payload["fault_codes"] == ["A07089"]
        assert runtime_artifact.payload["data_window"]["sample_count"] == 50
        assert runtime_artifact.payload["latest_sample_time"]

        second_response = client.get(
            "/chat/stream",
            params={"message": "那就导出报告吧", "thread_id": thread_id},
        )
        assert second_response.status_code == 200
        second_complete = _complete_event(second_response.text)

    decision = second_complete["decision"]
    assert decision["report_source_mode"] in {"reuse_artifact", "refresh_sql"}
    assert decision["report_source_mode"] != "blocked_missing_context"
    assert decision["report_source_mode"] != "ambiguous"
    assert decision["selected_artifact_id"] == runtime_artifact.created_at
    assert second_complete["report_artifact"]["success"] is True
    assert save_report_inputs
    serialized_report_payload = json.dumps(save_report_inputs[-1], ensure_ascii=False, default=str)
    assert "G120电机1" in serialized_report_payload
    assert "A07089" in serialized_report_payload
    assert "real_data_01" in serialized_report_payload
    assert "50" in serialized_report_payload
    assert runtime_artifact.payload["data_window"]["start"] in serialized_report_payload
    assert runtime_artifact.payload["data_window"]["end"] in serialized_report_payload
    assert second_complete["decision"]["report_candidate_artifact_count"] >= 1
    assert second_complete["decision"]["report_candidate_summary"][0]["reason"] == "selected"


def _j1_runtime_rows(count: int) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for index in range(count):
        minute = index % 60
        sample_time = f"2026-07-03 09:{minute:02d}:00"
        rows.append(
            (
                1000 + index,
                sample_time,
                "G120电机1",
                "J1",
                "2026-07-03",
                f"09:{minute:02d}:00",
                "warning",
                "A07089",
                "A07089",
                0,
                0,
                620.0,
                1000.0,
                720.0,
                12.3,
                0.0,
                0.0,
                28.0,
                56.0,
                42.0,
                31.5,
                0.0,
                0.0,
                3600.0,
                45.0,
                78.47,
                72.1,
                4.0,
                55.0,
                0.0,
                sample_time,
            )
        )
    return rows


def _complete_event(response_text: str) -> dict[str, Any]:
    for block in response_text.split("\n\n"):
        event_name = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if event_name == "complete" and data_lines:
            payload = json.loads("\n".join(data_lines))
            if isinstance(payload, dict):
                return payload
    raise AssertionError(f"complete event not found in response: {response_text[:500]}")
