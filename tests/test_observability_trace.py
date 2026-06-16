from __future__ import annotations

from fault_diagnosis.observability.payloads import sanitize_trace_value
from fault_diagnosis.observability.tracing import NoopTraceRun, TraceRunContext


def test_agent_console_compact_fields_hide_noisy_payloads(monkeypatch) -> None:
    from fault_diagnosis.single_agent import runner as runner_module

    monkeypatch.setattr(runner_module, "AGENT_TRACE_CONSOLE_VERBOSE", False)
    runner = runner_module.RestrictedSingleAgentRunner(
        message="生成dcma系统最近的运行报告",
        thread_id="thread.test",
        user_identity="游客",
        request_id="request.test",
        stream_id="stream.test",
        trace_id="trace.test",
    )

    compact = runner._compact_console_fields(
        {
            "stage": "sql",
            "status": "completed",
            "input_preview": {"query": "SELECT * FROM real_data_01"},
            "result_preview": [("large", "result")],
            "decision": {"needs_sql": True},
            "summary": "查询 real_data_01 最近 50 条运行状态、异常码和关键运行指标，用于生成 DCMA 运行报告。",
        }
    )

    assert compact["stage"] == "sql"
    assert compact["status"] == "completed"
    assert "real_data_01" in compact["summary"]
    assert "input_preview" not in compact
    assert "result_preview" not in compact
    assert "decision" not in compact


def test_sanitize_trace_value_redacts_sensitive_fields() -> None:
    payload = {
        "api_key": "sk-test-secret",
        "password": "super-secret",
        "nested": {"token": "token-abc"},
    }

    sanitized = sanitize_trace_value(payload, capture_content=True, preview_chars=80)

    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"


def test_noop_trace_run_lifecycle_is_safe() -> None:
    trace_run = NoopTraceRun(
        trace_context=TraceRunContext(
            trace_id="trace_123",
            request_id="req_123",
            thread_id="thread_123",
            user_identity="tester",
            user_message="hello",
        )
    )

    stage = trace_run.start_observation(name="stage")
    stage.update(output={"status": "ok"}).finish(status="completed")

    with trace_run.observation(name="child") as child:
        child.update(input={"hello": "world"})

    trace_run.finish(status="completed", output="done")
    trace_run.flush()
    trace_run.close()
