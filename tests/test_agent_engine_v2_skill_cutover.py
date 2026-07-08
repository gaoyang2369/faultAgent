from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI

from fault_diagnosis import config
from fault_diagnosis.agent_runtime import streaming
from fault_diagnosis.security.permissions import build_auth_context


def _events(chunks: list[str]) -> list[dict]:
    parsed = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            data = [line.removeprefix("data:").strip() for line in block.splitlines() if line.startswith("data:")]
            if data:
                parsed.append(json.loads("\n".join(data)))
    return parsed


class FakeToolRuntime:
    def invoke_sql_tool(self, tool_name: str, payload):  # noqa: ANN001
        assert tool_name == "sql_db_query"
        return [
            (
                1,
                "2026-07-08 10:00:00",
                "G120电机1",
                "INV-J1",
                "2026-07-08",
                "10:00:00",
                "异常",
                "A07089",
                "",
                "0",
                "0",
                560.0,
                1000.0,
                700.0,
                12.0,
                10.0,
                8.0,
                32.0,
                72.0,
                66.0,
                11.0,
                1.0,
                1.0,
                100.0,
                68.0,
                82.0,
                81.0,
                50.0,
                12.0,
                2.0,
                "2026-07-08 10:00:00",
            )
        ]

    def query_knowledge_base(self, query: str) -> str:
        return f"故障码：A07089\n含义：速度偏差或负载异常。\n查询：{query}"

    def save_report(self, **kwargs):  # noqa: ANN003
        return "报告已保存至：/reports/fake.html"


def test_fault_code_explain_can_run_v2_stream(monkeypatch) -> None:
    asyncio.run(_assert_fault_code_explain_can_run_v2_stream(monkeypatch))


async def _assert_fault_code_explain_can_run_v2_stream(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    app = FastAPI()
    app.state.dev_mode = False
    app.state.agent_engine_v2_tool_runtime = FakeToolRuntime()

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "A07089 是什么意思",
            "thread.v2.fault-code",
            request_id="request.v2.fault-code",
            auth_context=build_auth_context(role="guest"),
        )
    ]

    events = _events(chunks)
    complete = next(event for event in events if event["type"] == "chat_complete")
    assert complete["runtime"] == "agent_engine_v2"
    assert complete["knowledge_artifact"]["success"] is True
    assert any(event["type"] == "tool_start" and event["tool"] == "query_knowledge_base" for event in events)
    assert any(event["type"] == "tool_end" and event["tool"] == "query_knowledge_base" for event in events)


def test_runtime_status_can_run_v2_stream_with_deterministic_sql(monkeypatch) -> None:
    asyncio.run(_assert_runtime_status_can_run_v2_stream_with_deterministic_sql(monkeypatch))


async def _assert_runtime_status_can_run_v2_stream_with_deterministic_sql(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    app = FastAPI()
    app.state.dev_mode = False
    app.state.agent_engine_v2_tool_runtime = FakeToolRuntime()

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 当前运行状态怎么样",
            "thread.v2.runtime",
            request_id="request.v2.runtime",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["runtime"] == "agent_engine_v2"
    assert complete["sql_artifact"]["success"] is True
    assert "SELECT" in complete["sql_artifact"]["sql_used"][0]


def test_alarm_triage_runs_in_v2_without_legacy_fallback(monkeypatch) -> None:
    asyncio.run(_assert_alarm_triage_runs_in_v2_without_legacy_fallback(monkeypatch))


async def _assert_alarm_triage_runs_in_v2_without_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    app = FastAPI()
    app.state.dev_mode = False
    app.state.agent_engine_v2_tool_runtime = FakeToolRuntime()

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 的 A07089 现在还在报警吗，怎么处理",
            "thread.v2.alarm",
            request_id="request.v2.alarm",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["runtime"] == "agent_engine_v2"
    assert complete["sql_artifact"]["success"] is True
    assert complete["knowledge_artifact"]["success"] is True


def test_root_cause_runs_in_v2_without_legacy_fallback(monkeypatch) -> None:
    asyncio.run(_assert_root_cause_runs_in_v2_without_legacy_fallback(monkeypatch))


async def _assert_root_cause_runs_in_v2_without_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    app = FastAPI()
    app.state.dev_mode = False
    app.state.agent_engine_v2_tool_runtime = FakeToolRuntime()

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 为什么出现 A07089，帮我排查根因",
            "thread.v2.root",
            request_id="request.v2.root",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["runtime"] == "agent_engine_v2"
    assert complete["analysis_artifact"]["success"] is True


def test_workorder_decision_blocks_in_v2_without_dispatch_or_legacy_fallback(monkeypatch) -> None:
    asyncio.run(_assert_workorder_decision_blocks_in_v2_without_dispatch_or_legacy_fallback(monkeypatch))


async def _assert_workorder_decision_blocks_in_v2_without_dispatch_or_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    app = FastAPI()
    app.state.dev_mode = False
    app.state.agent_engine_v2_tool_runtime = FakeToolRuntime()

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 A07089 是否需要生成工单",
            "thread.v2.workorder",
            request_id="request.v2.workorder",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["runtime"] == "agent_engine_v2"
    assert complete["status"] in {"blocked", "completed", "failed"}
    assert not any("dispatch" in str(event).casefold() and event["type"] == "tool_start" for event in _events(chunks))


def test_report_generation_without_source_is_v2_failure_not_legacy_fallback(monkeypatch) -> None:
    asyncio.run(_assert_report_generation_without_source_is_v2_failure_not_legacy_fallback(monkeypatch))


async def _assert_report_generation_without_source_is_v2_failure_not_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    app = FastAPI()
    app.state.dev_mode = False
    app.state.agent_engine_v2_tool_runtime = FakeToolRuntime()

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "基于刚才结果生成报告",
            "thread.v2.report.missing",
            request_id="request.v2.report.missing",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["runtime"] == "agent_engine_v2"
    assert complete["status"] in {"failed", "blocked"}
