from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fault_diagnosis.agent_runtime.sse_adapter import parse_sse_chunk
from fault_diagnosis.observability.tracing import NoopTraceRun
from fault_diagnosis.single_agent.intent import (
    build_lightweight_conversation_reply,
    fallback_understanding_payload,
    normalize_equipment_hint,
    normalize_lightweight_message,
    should_use_rule_based_understanding,
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


def test_fallback_understanding_extracts_fault_code_before_chinese_text() -> None:
    payload = fallback_understanding_payload("查询故障代码F01002的触发原因", "游客")

    assert payload["fault_code_hint"] == "F01002"
    assert payload["needs_knowledge"] is True


def test_rule_based_understanding_handles_dcma_status_report_fast_path() -> None:
    message = "最近dcma运行情况如何？有异常码？可以生成具体报告展示"
    payload = fallback_understanding_payload(message, "游客")

    assert should_use_rule_based_understanding(message) is True
    assert payload["equipment_hint"] is None
    assert payload["needs_sql"] is True
    assert payload["needs_knowledge"] is True
    assert payload["needs_report"] is True
    assert normalize_equipment_hint("dcma") is None


def test_stream_events_short_circuits_greeting_without_model(monkeypatch) -> None:
    asyncio.run(_assert_stream_events_short_circuits_greeting_without_model(monkeypatch))


def test_analysis_payload_sanitizes_unsupported_load_thresholds(monkeypatch) -> None:
    from fault_diagnosis.single_agent import runner as runner_module

    monkeypatch.setattr(runner_module, "get_trace_exporter", lambda: _NoopTraceExporter())
    runner = runner_module.RestrictedSingleAgentRunner(
        message="诊断一下DCMA系统最近情况如何",
        thread_id="thread.test",
        user_identity="游客",
        request_id="request.test",
        stream_id="stream.test",
        trace_id="trace.test",
    )

    artifact = runner._build_analysis_artifact_from_payload(
        {
            "conclusion": "存在故障码",
            "basis": ["负载率最高78.47%，处于关注区间，需检查机械传动和工艺负载。"],
            "recommendations": ["临时措施：若无法立即停机，先降载至50%以下运行，避免风险。"],
            "risk_notice": "按现场规程确认安全状态。",
            "missing_information": [],
            "confidence": "medium",
        }
    )

    assert "50%" not in "\n".join(artifact.recommendations)
    assert "按现场规程降载" in artifact.recommendations[0]
    assert artifact.basis == ["负载率最高78.47%，处于关注区间"]


def test_analysis_payload_routes_actions_out_of_verification_items(monkeypatch) -> None:
    from fault_diagnosis.single_agent import runner as runner_module

    monkeypatch.setattr(runner_module, "get_trace_exporter", lambda: _NoopTraceExporter())
    runner = runner_module.RestrictedSingleAgentRunner(
        message="诊断一下DCMA系统最近情况如何",
        thread_id="thread.test",
        user_identity="游客",
        request_id="request.test",
        stream_id="stream.test",
        trace_id="trace.test",
    )

    artifact = runner._build_analysis_artifact_from_payload(
        {
            "conclusion": "存在故障码",
            "probable_causes": ["参数单位转换后未正确恢复，导致功能块无法激活，影响速度闭环。"],
            "verification_items": [
                "按RAG手册将单位参数恢复出厂设置，并观察故障码是否消失。",
                "检查速度闭环链路：运行使能状态、速度给定来源和编码器反馈信号完整性。",
            ],
            "recommendations": [],
            "missing_information": ["需要确认现场操作历史"],
            "confidence": "medium",
        }
    )

    assert "导致" not in artifact.probable_causes[0]
    assert "影响速度闭环" not in artifact.probable_causes[0]
    assert any("按RAG手册将单位参数恢复出厂设置" in item for item in artifact.recommendations)
    assert all("按RAG手册" not in item for item in artifact.verification_items)
    assert any("速度闭环链路" in item for item in artifact.verification_items)
    assert artifact.missing_information == ["现场操作历史"]


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
