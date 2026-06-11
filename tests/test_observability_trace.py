from __future__ import annotations

from fault_diagnosis.observability.payloads import sanitize_trace_value
from fault_diagnosis.observability.tracing import NoopTraceRun, TraceRunContext


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

