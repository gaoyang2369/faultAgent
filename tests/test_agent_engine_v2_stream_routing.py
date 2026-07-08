from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI

from fault_diagnosis import config
from fault_diagnosis.agent_runtime import streaming
from fault_diagnosis.agent_runtime.sse_adapter import encode_sse_event
from fault_diagnosis.security.permissions import build_auth_context


def _events(chunks: list[str]) -> list[dict]:
    parsed = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            data = [line.removeprefix("data:").strip() for line in block.splitlines() if line.startswith("data:")]
            if data:
                parsed.append(json.loads("\n".join(data)))
    return parsed


class _LegacyRunner:
    called = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def stream_events(self, app, cancel_handle=None):  # noqa: ANN001, ARG002
        type(self).called += 1
        yield encode_sse_event(
            "complete",
            {"type": "chat_complete", "thread_id": self.kwargs["thread_id"], "final_content": "legacy-ok"},
            trace_id=self.kwargs["trace_id"],
        )


def test_v2_shadow_keeps_legacy_stream_and_records_compare(monkeypatch, tmp_path) -> None:
    asyncio.run(_assert_v2_shadow_keeps_legacy_stream_and_records_compare(monkeypatch, tmp_path))


async def _assert_v2_shadow_keeps_legacy_stream_and_records_compare(monkeypatch, tmp_path) -> None:
    _LegacyRunner.called = 0
    records = []
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2_shadow")
    monkeypatch.setattr(config, "AGENT_ENGINE_V2_COMPARE_LOG_PATH", str(tmp_path / "compare.jsonl"))
    monkeypatch.setenv("AGENT_ENGINE_V2_SKILL_RUNTIME_STATUS", "shadow")
    monkeypatch.setattr(streaming, "RestrictedSingleAgentRunner", _LegacyRunner)
    monkeypatch.setattr(streaming, "record_plan_compare", lambda record: records.append(record))
    app = FastAPI()
    app.state.dev_mode = False

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 当前运行状态怎么样",
            "thread.shadow",
            request_id="request.shadow",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["final_content"] == "legacy-ok"
    assert _LegacyRunner.called == 1
    assert records and records[0]["primary_skill"] == "runtime_status"
    assert records[0]["effective_skill_mode"] == "shadow"


def test_v2_compare_failure_does_not_break_legacy_stream(monkeypatch) -> None:
    asyncio.run(_assert_v2_compare_failure_does_not_break_legacy_stream(monkeypatch))


async def _assert_v2_compare_failure_does_not_break_legacy_stream(monkeypatch) -> None:
    class BrokenEngine:
        def plan_only(self, **kwargs):  # noqa: ANN001, ARG002
            raise RuntimeError("compare boom")

    _LegacyRunner.called = 0
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2_shadow")
    monkeypatch.setattr(streaming, "AgentEngineV2", BrokenEngine)
    monkeypatch.setattr(streaming, "RestrictedSingleAgentRunner", _LegacyRunner)
    app = FastAPI()
    app.state.dev_mode = False

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 当前运行状态怎么样",
            "thread.shadow.failure",
            request_id="request.shadow.failure",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["final_content"] == "legacy-ok"
    assert _LegacyRunner.called == 1


def test_v2_mode_falls_back_when_skill_not_ready(monkeypatch) -> None:
    asyncio.run(_assert_v2_mode_falls_back_when_skill_not_ready(monkeypatch))


async def _assert_v2_mode_falls_back_when_skill_not_ready(monkeypatch) -> None:
    _LegacyRunner.called = 0
    records = []
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    monkeypatch.setenv("AGENT_ENGINE_V2_SKILL_ALARM_TRIAGE", "v2")
    monkeypatch.setattr(streaming, "RestrictedSingleAgentRunner", _LegacyRunner)
    monkeypatch.setattr(streaming, "record_plan_compare", lambda record: records.append(record))
    app = FastAPI()
    app.state.dev_mode = False

    chunks = [
        chunk
        async for chunk in streaming.token_stream_events(
            app,
            "J1 的 A07089 现在还在报警吗，怎么处理",
            "thread.v2.fallback",
            request_id="request.v2.fallback",
            auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        )
    ]

    complete = next(event for event in _events(chunks) if event["type"] == "chat_complete")
    assert complete["final_content"] == "legacy-ok"
    assert _LegacyRunner.called == 1
    assert records[0]["fallback_reason"].endswith("not_enabled_in_phase9")
