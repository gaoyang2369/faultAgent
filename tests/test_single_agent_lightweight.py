from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fault_diagnosis.agent_runtime.sse_adapter import parse_sse_chunk
from fault_diagnosis.observability.tracing import NoopTraceRun
from fault_diagnosis.single_agent.intent import (
    build_lightweight_conversation_reply,
    normalize_lightweight_message,
)


class _NoopTraceExporter:
    def start_run(self, trace_context):
        return NoopTraceRun(trace_context=trace_context)


class _ExplodingModel:
    async def ainvoke(self, prompt: str):  # pragma: no cover - should never be called
        raise AssertionError(f"model should not be called for lightweight replies: {prompt}")


def test_lightweight_greeting_gets_standard_reply() -> None:
    reply = build_lightweight_conversation_reply("你好！")

    assert reply == "你好，我是故障诊断智能助手。有什么可以帮助你的吗？你也可以直接告诉我设备型号、故障码或异常现象。"


def test_diagnostic_message_with_greeting_is_not_short_circuited() -> None:
    assert build_lightweight_conversation_reply("你好，设备A最近报警") is None
    assert build_lightweight_conversation_reply("hello E1234 是什么意思") is None


def test_lightweight_normalization_handles_common_punctuation() -> None:
    assert normalize_lightweight_message(" Hello？！ ") == "hello"


def test_stream_events_short_circuits_greeting_without_model(monkeypatch) -> None:
    asyncio.run(_assert_stream_events_short_circuits_greeting_without_model(monkeypatch))


async def _assert_stream_events_short_circuits_greeting_without_model(monkeypatch) -> None:
    from fault_diagnosis.single_agent import runner as runner_module

    monkeypatch.setattr(runner_module, "get_trace_exporter", lambda: _NoopTraceExporter())
    app = SimpleNamespace(state=SimpleNamespace(chat_model=_ExplodingModel()))
    runner = runner_module.RestrictedSingleAgentRunner(
        message="你好",
        thread_id="thread.test",
        user_identity="游客",
        request_id="request.test",
        stream_id="stream.test",
        trace_id="trace.test",
    )

    chunks = [chunk async for chunk in runner.stream_events(app)]
    parsed = [parse_sse_chunk(chunk) for chunk in chunks]

    assert [item[0] for item in parsed if item] == ["start", "token", "complete"]
    token_payload = parsed[1][1]
    complete_payload = parsed[2][1]
    assert "故障诊断智能助手" in token_payload["content"]
    assert complete_payload["final_content"] == token_payload["content"]
    assert complete_payload["decision"]["reason"] == "轻量问候直接回答"
    assert runner._tool_call_count == 0
