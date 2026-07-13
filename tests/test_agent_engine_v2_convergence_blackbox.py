from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import fault_diagnosis.platform.settings as settings
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.file import FileArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    configure_artifact_store_backend,
    get_canonical_artifact,
    reset_artifact_store_backend,
)
from fault_diagnosis.platform.persistence.repositories.conversation_store import SQLiteConversationRepository
from fault_diagnosis.platform.persistence.repositories.history_index import MemoryHistoryIndexRepository
from fault_diagnosis.server.auth.session_scope import SessionScopeManager
from fault_diagnosis.server.http.routers.auth import router as auth_router
from fault_diagnosis.server.http.routers.chat import router as chat_router


class _ExternalAdapters:
    def invoke_sql_tool(self, tool_name: str, payload):  # noqa: ANN001
        assert tool_name in {"sql_db_query", "sql_db_query_checker"}
        if tool_name == "sql_db_query_checker":
            return payload
        text = str(payload)
        device = "J1号机" if "J1" in text or "real_data_02" in text else "G120电机1"
        return [_runtime_row(device)]

    def query_knowledge_base(self, query: str) -> str:
        return (
            "故障码：A07089\n"
            "标题：速度偏差\n"
            "含义：实际速度与设定速度偏差超限。\n"
            "原因：负载突变或速度反馈异常。\n"
            "处理：检查负载、编码器和速度环参数。\n"
            "来源文件：internal-chunk-should-not-render.pdf\n"
            f"查询：{query}"
        )

    def save_report(self, **kwargs):  # noqa: ANN003
        return "报告已保存至：/reports/convergence-report.html"


class _ReadbackFailureStore(FileArtifactStoreBackend):
    def get_artifact(self, thread_id: str, artifact_id: str):  # noqa: ANN201, ARG002
        return None


def _runtime_row(device: str) -> tuple:
    values = [None] * 32
    values[0] = 1
    values[1] = "2026-07-13 10:00:00"
    values[2] = device
    values[3] = "INV-01"
    values[4] = "2026-07-13"
    values[5] = "10:00:00"
    values[6] = "异常"
    values[7] = "A07089"
    values[11] = 620
    values[12] = 1500
    values[13] = 1380 if device == "G120电机1" else 1460
    values[18] = 68 if device == "G120电机1" else 54
    values[19] = 72 if device == "G120电机1" else 58
    values[25] = 88 if device == "G120电机1" else 64
    values[26] = 84 if device == "G120电机1" else 61
    values[31] = "2026-07-13 10:00:00"
    return tuple(values)


def _events(response) -> list[dict]:  # noqa: ANN001
    result: list[dict] = []
    for block in response.text.split("\n\n"):
        data = [line.removeprefix("data:").strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            result.append(json.loads("\n".join(data)))
    return result


def _turn(client: TestClient, message: str, thread_id: str | None = None) -> tuple[list[dict], dict]:
    params = {"message": message}
    if thread_id:
        params["thread_id"] = thread_id
    response = client.get("/chat/stream", params=params)
    assert response.status_code == 200
    events = _events(response)
    complete = next(item for item in events if item.get("type") == "chat_complete")
    assert next(item for item in events if item.get("type") == "token")["content"] == complete["content"]
    assert complete["content"] == complete["final_content"] == complete["composite_output"]["content"]
    return events, complete


def _client(monkeypatch, tmp_path, *, artifact_backend=None) -> TestClient:  # noqa: ANN001
    monkeypatch.setattr(settings, "DEV_AUTH_ENABLED", True)
    configure_artifact_store_backend(artifact_backend or FileArtifactStoreBackend(root_dir=tmp_path / "artifacts"))
    app = FastAPI()
    app.state.dev_mode = False
    app.state.session_scope_manager = SessionScopeManager("convergence-blackbox-secret")
    app.state.conversation_repository = SQLiteConversationRepository(tmp_path / "conversation.sqlite3")
    app.state.history_index_repository = MemoryHistoryIndexRepository()
    app.state.agent_engine_v2_tool_runtime = _ExternalAdapters()
    app.include_router(auth_router)
    app.include_router(chat_router)
    client = TestClient(app)
    login = client.post(
        "/auth/dev-login",
        json={
            "role": "engineer",
            "user_id": "engineer-convergence",
            "asset_scope": ["g120_motor_1", "J1号机"],
            "allowed_tables": ["real_data_01", "real_data_02"],
        },
    )
    assert login.status_code == 200
    return client


def test_e01_three_turn_analysis_id_is_exact_and_third_turn_runs_report_only(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        _, first = _turn(client, "查询 G120电机1 当前运行状态")
        thread_id = first["thread_id"]
        _, second = _turn(client, "基于上一轮数据诊断故障", thread_id)
        analysis_ref = next(item for item in second["produced_artifacts"] if item["artifact_type"] == "analysis_artifact")
        analysis_id = analysis_ref["artifact_id"]
        stored = get_canonical_artifact(thread_id, analysis_id)
        assert stored is not None and stored.readback_verified and stored.persistence_status == "committed"
        assert stored.artifact_id == stored.manifest.artifact_id == analysis_ref["manifest"]["artifact_id"]

        third_events, third = _turn(client, "基于诊断生成报告", thread_id)
        node_types = [item["node_type"] for item in third["node_results"]]
        assert node_types == ["report"]
        report_ref = next(item for item in third["produced_artifacts"] if item["artifact_type"] == "report_artifact")
        assert report_ref["manifest"]["lineage"]["source_artifact_ids"] == [analysis_id]
        assert not any(item.get("stage") in {"sql", "analysis"} for item in third_events if item.get("type") == "tool_start")
    finally:
        client.close()
        reset_artifact_store_backend()


def test_e03_four_goals_have_four_unique_deliverables_and_no_raw_chunk_metadata(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        _, complete = _turn(
            client,
            "解释 A07089，并查询 G120电机1 当前状态，诊断原因并给出处理建议",
        )
        goals = complete["goal_set"]["goals"]
        deliverables = complete["composite_output"]["deliverables"]
        assert len(goals) == len(deliverables) == 4
        assert len({item["goal_id"] for item in deliverables}) == 4
        assert complete["content"].count("【故障码解释】") == 1
        assert complete["content"].count("【运行状态】") == 1
        assert complete["content"].count("【综合诊断】") == 1
        assert complete["content"].count("【处理建议】") == 1
        assert "internal-chunk-should-not-render.pdf" not in complete["content"]
        assert "raw_output" not in complete["content"]
    finally:
        client.close()
        reset_artifact_store_backend()


def test_a01_comparison_has_two_sql_sources_and_cross_device_claim(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        _, complete = _turn(client, "比较 G120电机1 和 J1号机 当前运行状态")
        node_types = [item["node_type"] for item in complete["node_results"]]
        assert node_types.count("sql") == 2
        assert node_types[-1] == "comparison"
        comparison = complete["composite_output"]["deliverables"][0]["payload"]
        assert comparison["devices"] == ["G120电机1", "J1号机"]
        assert len(comparison["source_artifact_ids"]) == 2
        claim = next(item for item in complete["evidence_bundle"]["claims"] if item["claim_type"] == "runtime_comparison")
        assert len(claim["supporting_evidence_ids"]) == 2
        assert "其余设备" not in complete["content"]
    finally:
        client.close()
        reset_artifact_store_backend()


def test_a02_third_turn_runs_workorder_then_pending_approval_without_dispatch(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        _, first = _turn(client, "查询并分析 G120电机1 的 A07089 故障")
        thread_id = first["thread_id"]
        _, second = _turn(client, "基于诊断生成报告", thread_id)
        report_id = next(item["artifact_id"] for item in second["produced_artifacts"] if item["artifact_type"] == "report_artifact")
        _, third = _turn(client, "根据报告生成工单草稿", thread_id)
        assert [item["node_type"] for item in third["node_results"]] == ["workorder", "approval"]
        assert third["node_results"][0]["status"] == "completed"
        assert third["node_results"][1]["output"]["status"] == "pending_manual_confirmation"
        assert third["node_results"][1]["output"]["dispatch_performed"] is False
        draft = third["workorder_draft"]
        assert draft["status"] == "draft"
        assert third["status"] == "completed"
        assert third["manual_confirmation"]["required"] is True
        assert report_id in {item["artifact_id"] for item in third["workorder_draft_payload"]["source_artifact_refs"]}

        _, fourth = _turn(client, "确认工单草稿", thread_id)
        assert [item["node_type"] for item in fourth["node_results"]] == ["workorder", "approval"]
        assert fourth["workorder_pending_action"]["status"] == "confirmed_pending_dispatch_forbidden"
        assert fourth["node_results"][0]["output"]["dispatch_performed"] is False
    finally:
        client.close()
        reset_artifact_store_backend()


def test_staged_commit_readback_failure_does_not_publish_conversation_ref(monkeypatch, tmp_path) -> None:
    backend = _ReadbackFailureStore(root_dir=tmp_path / "artifacts")
    client = _client(monkeypatch, tmp_path, artifact_backend=backend)
    try:
        _, complete = _turn(client, "查询 G120电机1 当前运行状态")
        assert complete["produced_artifacts"] == []
        assert complete["artifact"]["payload"]["artifact_manifests"] == []
        assert complete["artifact"]["payload"]["artifact_commit_failures"]
    finally:
        client.close()
        reset_artifact_store_backend()
