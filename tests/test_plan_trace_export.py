from __future__ import annotations

from fault_diagnosis.agent import AgentEngineV2
from fault_diagnosis.platform.observability import TraceRecorder


def test_plan_snapshot_exports_canonical_plan_spans_before_runtime() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="A07089 是什么意思",
        thread_id="thread.plan",
        request_id="request.plan",
    )
    recorder = TraceRecorder(trace_id="trace.plan", request_id="request.plan", thread_id="thread.plan")
    recorder.add_plan_snapshot(snapshot)
    envelope = recorder.finish()

    names = [span.name for span in envelope.spans]

    assert names[:9] == [
        "chat.request",
        "semantic.resolve",
        "request.understand",
        "context.resolve",
        "capability.preflight",
        "goal.build",
        "skill.route",
        "plan.compile",
        "plan.validate",
    ]
    assert all(span.parent_span_id == "span.chat.request" for span in envelope.spans[1:8])
    assert envelope.spans[2].attributes["detected_fault_codes"] == ["A07089"]
