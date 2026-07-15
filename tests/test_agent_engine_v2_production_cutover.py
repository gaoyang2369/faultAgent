from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import fault_diagnosis.platform.settings as settings
import fault_diagnosis.server.bootstrap.app_lifespan as lifespan_module
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.file import FileArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    configure_artifact_store_backend,
    reset_artifact_store_backend,
)
from fault_diagnosis.platform.persistence.repositories.conversation_store import SQLiteConversationRepository
from fault_diagnosis.platform.persistence.repositories.history_index import MemoryHistoryIndexRepository
from fault_diagnosis.server.auth.session_scope import SessionScopeManager
from fault_diagnosis.server.bootstrap.app_factory import create_app


@dataclass
class FrontendTurn:
    events: list[tuple[str, dict[str, Any]]]
    complete: dict[str, Any]
    token_content: str
    visible_content: str
    persisted: dict[str, Any]


class ProductionTestAdapters:
    """Only replace external SQL/KB/report boundaries used by the real services."""

    def __init__(self) -> None:
        self.sql_calls: list[dict[str, str]] = []

    def invoke_sql_tool(self, tool_name: str, payload: Any) -> Any:
        text = str(payload)
        self.sql_calls.append({"tool": tool_name, "payload": text})
        if tool_name == "sql_db_query_checker":
            return payload
        assert tool_name == "sql_db_query"
        if "real_data_02" in text:
            return [_runtime_row(device="G120电机2", fault_code="F30899")]
        return [_runtime_row(device="G120电机1", fault_code="A07089")]

    def query_knowledge_base(self, query: str) -> str:
        code = "F30899" if "F30899" in query else "A07089"
        return (
            f"故障码：{code}\n"
            f"标题：{'电机过载' if code == 'F30899' else '速度偏差'}\n"
            f"含义：{'驱动检测到过载状态' if code == 'F30899' else '实际速度与设定速度偏差超限'}。\n"
            "原因：负载突变或速度反馈异常。\n"
            "处理：检查负载、编码器和速度环参数。\n"
            "来源文件：production-cutover-fixture.pdf"
        )

    def save_report(self, **kwargs: Any) -> str:
        filename = str(kwargs.get("report_filename") or "production-cutover-report.html")
        return f"报告已保存至：/reports/{filename}"


@pytest.fixture
def production_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repository = SQLiteConversationRepository(tmp_path / "conversation.sqlite3")
    adapters = ProductionTestAdapters()

    async def _noop_async() -> None:
        return None

    monkeypatch.setattr(settings, "DEV_AUTH_ENABLED", True)
    monkeypatch.setattr(lifespan_module, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(lifespan_module, "init_pool", _noop_async)
    monkeypatch.setattr(lifespan_module, "close_pool", _noop_async)
    monkeypatch.setattr(lifespan_module, "has_knowledge_base_index", lambda: False)
    monkeypatch.setattr(lifespan_module, "get_conversation_repository", lambda: repository)
    configure_artifact_store_backend(FileArtifactStoreBackend(root_dir=tmp_path / "artifacts"))

    app = create_app()
    app.state.session_scope_manager = SessionScopeManager("agent-v2-production-cutover-secret")
    app.state.history_index_repository = MemoryHistoryIndexRepository()
    app.state.agent_engine_v2_tool_runtime = adapters

    with TestClient(app) as client:
        login = client.post(
            "/auth/dev-login",
            json={
                "role": "admin",
                "user_id": "cutover-admin",
                "asset_scope": ["g120_motor_1", "g120_motor_2", "G120电机1", "G120电机2"],
                "allowed_tables": ["real_data_01", "real_data_02"],
            },
        )
        assert login.status_code == 200, login.text
        yield client, repository.path, adapters

    reset_artifact_store_backend()


def test_status_transport_is_identical_through_composite_sse_and_sqlite(production_harness) -> None:
    client, db_path, _ = production_harness
    turn = _turn(client, db_path, "查询 G120电机1 当前运行状态")

    complete = turn.complete
    _assert_transport_consistency("status", turn)
    assert complete["sql_artifact"]["success"] is True
    assert complete["rendered_answer"]["runtime_status_assessment"]
    assert complete["composite_output"]["overall_status"] == "completed"
    assert turn.visible_content == complete["final_content"]
    assert turn.persisted["metadata"]["content_fingerprint"] == {
        "length": len(turn.persisted["content_text"]),
        "sha256": hashlib.sha256(turn.persisted["content_text"].encode("utf-8")).hexdigest(),
    }


def test_status_selected_content_keeps_human_readable_status_fields(production_harness) -> None:
    client, db_path, _ = production_harness
    turn = _turn(client, db_path, "查询 G120电机1 当前运行状态")
    content = turn.visible_content
    assert "G120电机1" in content
    assert "异常" in content or "关注" in content
    assert "数据模式：" in content
    assert "最新样本：" in content


def test_four_goal_composite_has_one_renderer_path_and_no_legacy_template(production_harness) -> None:
    client, db_path, _ = production_harness
    turn = _turn(
        client,
        db_path,
        "解释 A07089，并查询 G120电机1 当前状态，诊断原因并给出处理建议",
    )
    complete = turn.complete
    goals = complete["goal_set"]["goals"]
    deliverables = complete["composite_output"]["deliverables"]
    observation = complete["trace"]["output_observation"]
    print(
        "CUTOVER four_goal",
        json.dumps(
            {
                "requested_goal_ids": [item["goal_id"] for item in goals],
                "deliverables": [
                    {"goal_id": item["goal_id"], "type": item["deliverable_type"], "status": item["status"]}
                    for item in deliverables
                ],
                "output_observation": observation,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

    assert len(goals) == len(deliverables) == 4
    assert len({item["goal_id"] for item in deliverables}) == 4
    _assert_transport_consistency("four_goal", turn)
    assert observation["selected_content"] == "composite"
    assert observation["renderer_calls"] == [
        "DeliverableAssembler.assemble",
        "CompositePresenter.render",
    ]
    assert observation["legacy_answer_template"] == ""
    assert observation["answer_template"] == "composite_presenter_v1"
    assert turn.visible_content.count("【故障码解释】") == 1
    assert turn.visible_content.count("【故障诊断】") == 1
    assert turn.visible_content.count("【运行状态】") == 1
    assert turn.visible_content.count("【处理建议】") == 1


def test_comparison_transport_is_identical_and_renders_once(production_harness) -> None:
    client, db_path, _ = production_harness
    turn = _turn(client, db_path, "比较 G120电机1 和 G120电机2 当前运行状态")

    _assert_transport_consistency("comparison", turn)
    content = turn.visible_content
    assert content.count("【运行比较】") == 1
    assert "G120电机1" in content
    assert "G120电机2" in content
    assert "结论：" in content
    assert turn.complete["rendered_answer"]["answer_variant"] != "status_brief_v2"


def test_singular_pronoun_after_comparison_only_runs_clarification(production_harness) -> None:
    client, db_path, _ = production_harness
    comparison = _turn(client, db_path, "比较 G120电机1 和 G120电机2 当前运行状态")
    followup = _turn(client, db_path, "它现在有没有故障？", comparison.complete["thread_id"])
    trace = _context_span(followup.complete)
    node_types = [item["node_type"] for item in followup.complete["node_results"]]
    print(
        "CUTOVER singular_pronoun",
        json.dumps(
            {
                "context": trace,
                "nodes": node_types,
                "produced_artifacts": followup.complete.get("produced_artifacts", []),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

    assert trace["resolution_status"] == "ambiguous"
    assert trace["selected_devices"] == []
    assert trace["selected_artifact_id"] in {"", None}
    assert followup.complete["produced_artifacts"] == []
    assert node_types == []
    assert trace["missing_context"] == ["device"]


def test_motor2_report_and_repeated_workorder_keep_typed_source_lineage(production_harness) -> None:
    client, db_path, adapters = production_harness
    turns: list[FrontendTurn] = []
    turns.append(_turn(client, db_path, "查询并诊断 G120电机2 的 F30899 故障"))
    thread_id = turns[0].complete["thread_id"]
    turns.append(_turn(client, db_path, "基于诊断生成报告", thread_id))
    turns.append(_turn(client, db_path, "根据报告生成工单草稿", thread_id))
    turns.append(_turn(client, db_path, "再次根据报告生成工单草稿", thread_id))

    observations = []
    for index, turn in enumerate(turns, start=1):
        observations.append(
            {
                "turn": index,
                "nodes": [item["node_type"] for item in turn.complete["node_results"]],
                "artifacts": [
                    {
                        "id": item.get("artifact_id"),
                        "type": item.get("artifact_type"),
                        "devices": (item.get("manifest") or {}).get("device_refs", []),
                        "source_table": (item.get("manifest") or {}).get("source_table"),
                        "lineage": (item.get("manifest") or {}).get("lineage", {}),
                    }
                    for item in turn.complete.get("produced_artifacts", [])
                ],
                "context": _context_span(turn.complete),
                "errors": turn.complete.get("trace", {}).get("errors", []),
            }
        )
    print(
        "CUTOVER motor2_workorder",
        json.dumps(
            {"turns": observations, "sql_calls": adapters.sql_calls},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

    all_manifests = [item for turn in observations for item in turn["artifacts"]]
    sql_manifests = [item for item in all_manifests if item["type"] == "sql_artifact"]
    assert sql_manifests
    assert all(item["devices"] == ["G120电机2"] for item in sql_manifests)
    assert all(item["source_table"] == "real_data_02" for item in sql_manifests)
    assert not any("real_data_01" in call["payload"] for call in adapters.sql_calls)
    assert observations[3]["context"]["selected_artifact_type"] in {"report_artifact", "analysis_artifact"}
    assert not any(error.get("code") == "target_artifact_type_mismatch" for error in observations[3]["errors"])
    third_workorder = next(item for item in observations[2]["artifacts"] if item["type"] == "workorder_artifact")
    fourth_workorder = next(item for item in observations[3]["artifacts"] if item["type"] == "workorder_artifact")
    assert fourth_workorder["id"] == third_workorder["id"]
    fourth_workorder_result = next(
        item for item in turns[3].complete["node_results"] if item["node_type"] == "workorder"
    )
    assert fourth_workorder_result["output"]["idempotency_result"] == "reused"
    fourth_workorder_trace = next(
        item
        for item in turns[3].complete["trace"]["events"]
        if item.get("event_type") == "node_status"
        and item.get("node_type") == "workorder"
        and item.get("status") == "completed"
    )
    assert fourth_workorder_trace["metadata"]["idempotency_result"] == "reused"


def _turn(client: TestClient, db_path: Path, message: str, thread_id: str | None = None) -> FrontendTurn:
    params = {"message": message}
    if thread_id:
        params["thread_id"] = thread_id
    response = client.get("/chat/stream", params=params)
    assert response.status_code == 200, response.text

    events: list[tuple[str, dict[str, Any]]] = []
    full_content = ""
    complete: dict[str, Any] | None = None
    for block in response.text.split("\n\n"):
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip() or event_name
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if not data_lines:
            continue
        payload = json.loads("\n".join(data_lines))
        events.append((event_name, payload))
        if event_name == "token" or payload.get("type") == "token":
            full_content += str(payload.get("content") or "")
        if event_name == "complete" or payload.get("type") == "chat_complete":
            complete = payload
            full_content = str(payload.get("final_content") or full_content)

    assert complete is not None, events
    persisted = _last_assistant_message(db_path, str(complete["thread_id"]))
    token_content = "".join(
        str(payload.get("content") or "")
        for event_name, payload in events
        if event_name == "token" or payload.get("type") == "token"
    )
    return FrontendTurn(
        events=events,
        complete=complete,
        token_content=token_content,
        visible_content=full_content,
        persisted=persisted,
    )


def _last_assistant_message(db_path: Path, thread_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT content_text, content_json, metadata_json, status
            FROM agent_messages
            WHERE thread_id = ? AND role = 'assistant' AND deleted_at IS NULL
            ORDER BY turn_index DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
    assert row is not None
    result = dict(row)
    result["content_json"] = json.loads(result["content_json"] or "{}")
    result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
    return result


def _runtime_row(*, device: str, fault_code: str) -> tuple[Any, ...]:
    values: list[Any] = [None] * 32
    values[0] = 1
    values[1] = "2026-07-13 10:00:00"
    values[2] = device
    values[3] = "INV-02" if device.endswith("2") else "INV-01"
    values[4] = "2026-07-13"
    values[5] = "10:00:00"
    values[6] = "异常"
    values[7] = fault_code
    values[11] = 620
    values[12] = 1500
    values[13] = 1320 if device.endswith("2") else 1380
    values[18] = 76 if device.endswith("2") else 68
    values[19] = 81 if device.endswith("2") else 72
    values[25] = 91 if device.endswith("2") else 88
    values[26] = 89 if device.endswith("2") else 84
    values[31] = "2026-07-13 10:00:00"
    return tuple(values)


def _print_content_fingerprints(label: str, contents: dict[str, str]) -> None:
    payload = {
        key: {"length": len(value), "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
        for key, value in contents.items()
    }
    print(f"CUTOVER {label}", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _assert_transport_consistency(label: str, turn: FrontendTurn) -> None:
    complete = turn.complete
    contents = {
        "composite": str(complete["composite_output"]["content"]),
        "token_join": turn.token_content,
        "complete.content": str(complete.get("content") or ""),
        "final_content": str(complete.get("final_content") or ""),
        "sqlite.assistant": str(turn.persisted["content_text"]),
    }
    _print_content_fingerprints(label, contents)
    assert len({value for value in contents.values()}) == 1, _first_mismatch(contents)


def _first_mismatch(contents: dict[str, str]) -> str:
    items = list(contents.items())
    reference_name, reference = items[0]
    for name, value in items[1:]:
        if value == reference:
            continue
        boundary = 0
        for boundary, (left, right) in enumerate(zip(reference, value)):
            if left != right:
                break
        else:
            boundary = min(len(reference), len(value))
        return f"{reference_name} != {name}; first mismatch at char {boundary}"
    return "all content values match"


def _context_span(complete: dict[str, Any]) -> dict[str, Any]:
    spans = complete.get("canonical_trace", {}).get("spans", [])
    span = next(item for item in spans if item.get("name") == "context.resolve")
    return dict(span.get("attributes") or {})
