from __future__ import annotations

from fault_diagnosis.platform.observability import TraceRecorder
from fault_diagnosis.platform.observability import trace_recorder


def test_canonical_trace_sanitizes_sensitive_attributes(monkeypatch) -> None:
    monkeypatch.setattr(trace_recorder, "AGENT_TRACE_CAPTURE_CONTENT", True)
    recorder = TraceRecorder(trace_id="trace.schema", request_id="request.schema", thread_id="thread.schema")
    recorder.add_error(error="failed with password", attributes={"mysql_pw": "secret", "nested": {"token": "abc"}})

    envelope = recorder.finish(status="failed")
    error_span = next(span for span in envelope.spans if span.name == "request.error")

    assert error_span.attributes["mysql_pw"] == "[REDACTED]"
    assert error_span.attributes["nested"]["token"] == "[REDACTED]"


def test_canonical_trace_capture_content_false_hashes_text(monkeypatch) -> None:
    monkeypatch.setattr(trace_recorder, "AGENT_TRACE_CAPTURE_CONTENT", False)
    long_text = "A07089 " * 100
    recorder = TraceRecorder(
        trace_id="trace.no_content",
        request_id="request.no_content",
        thread_id="thread.no_content",
        user_message=long_text,
    )

    root = recorder.finish().spans[0]

    assert "sha256_12" in root.attributes["message"]
    assert long_text.strip() not in str(root.attributes)
