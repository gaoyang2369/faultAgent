from __future__ import annotations

import json

from fault_diagnosis.platform.observability.trace_exporters import write_local_trace_envelope
from fault_diagnosis.platform.observability.trace_schema import TraceEnvelope


def test_trace_jsonl_writes_canonical_trace_and_legacy_events(tmp_path, monkeypatch) -> None:
    from fault_diagnosis.platform.observability import trace_exporters

    path = tmp_path / "agent-trace.jsonl"
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_LOCAL_LOG", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_LOCAL_LOG_PATH", str(path))
    envelope = TraceEnvelope(
        trace_id="trace.jsonl",
        request_id="request.jsonl",
        thread_id="thread.jsonl",
        stream_id="stream.jsonl",
        status="completed",
    )

    write_local_trace_envelope(envelope, legacy_runtime_events=[{"event_type": "node_status"}])

    written = json.loads(path.read_text(encoding="utf-8").strip())
    assert written["trace"]["schema_version"] == "agent_trace.v1"
    assert written["metadata"]["request_id"] == "request.jsonl"
    assert written["metadata"]["trace_id"] == "trace.jsonl"
    assert written["metadata"]["thread_id"] == "thread.jsonl"
    assert written["metadata"]["status"] == "completed"
    assert written["legacy_runtime_events"] == [{"event_type": "node_status"}]

