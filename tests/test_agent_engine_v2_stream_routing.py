from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI

from fault_diagnosis import config
from fault_diagnosis.server.agent_gateway import streaming
from fault_diagnosis.domain.security.permissions import build_auth_context


def _events(chunks: list[str]) -> list[dict]:
    parsed = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            data = [line.removeprefix("data:").strip() for line in block.splitlines() if line.startswith("data:")]
            if data:
                parsed.append(json.loads("\n".join(data)))
    return parsed


class _FakeToolRuntime:
    def invoke_sql_tool(self, tool_name: str, payload):  # noqa: ANN001
        assert tool_name == "sql_db_query"
        return [(1, "2026-07-08 10:00:00", "G120电机1", "INV-J1", "2026-07-08", "10:00:00", "正常", "", "")]

    def query_knowledge_base(self, query: str) -> str:
        return f"故障码：A07089\n含义：速度偏差或负载异常。\n查询：{query}"

    def save_report(self, **kwargs):  # noqa: ANN003
        return "报告已保存至：/reports/fake.html"


def test_default_stream_uses_v2_without_legacy_or_compare(monkeypatch) -> None:
    asyncio.run(_assert_default_stream_uses_v2_without_legacy_or_compare(monkeypatch))


async def _assert_default_stream_uses_v2_without_legacy_or_compare(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    app = FastAPI()
    app.state.dev_mode = False
    app.state.agent_engine_v2_tool_runtime = _FakeToolRuntime()

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "A07089 是什么意思",
            "thread.v2.default",
            request_id="request.v2.default",
            auth_context=build_auth_context(role="guest"),
        )
    ]

    events = _events(chunks)
    complete = next(event for event in events if event["type"] == "chat_complete")
    assert complete["runtime"] == "agent_engine_v2"
    assert any(event["type"] == "tool_start" for event in events)


def test_v2_failure_returns_server_error_without_legacy_fallback(monkeypatch) -> None:
    asyncio.run(_assert_v2_failure_returns_server_error_without_legacy_fallback(monkeypatch))


async def _assert_v2_failure_returns_server_error_without_legacy_fallback(monkeypatch) -> None:
    class BrokenEngine:
        def build_plan_snapshot(self, **kwargs):  # noqa: ANN001, ARG002
            raise RuntimeError("v2 boom")

    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    monkeypatch.setattr(streaming, "AgentEngineV2", BrokenEngine)
    app = FastAPI()
    app.state.dev_mode = False

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 当前运行状态怎么样",
            "thread.v2.failure",
            request_id="request.v2.failure",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    server_error = next(event for event in _events(chunks) if event.get("event_type") == "server_error")
    assert server_error["error"]["code"] == "INTERNAL_ERROR"
