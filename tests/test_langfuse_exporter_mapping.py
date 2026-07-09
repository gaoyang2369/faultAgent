from __future__ import annotations

from fault_diagnosis.platform.observability.trace_exporters import export_langfuse_trace_envelope
from fault_diagnosis.platform.observability.trace_schema import TraceEnvelope, TraceSpan


class FakeObservation:
    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self.calls = calls

    def finish(self, *, status: str, error=None):  # noqa: ANN001
        return None


class FakeTraceRun:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def start_observation(self, *, name: str, as_type: str = "span", **kwargs):  # noqa: ANN003
        self.calls.append((name, as_type))
        return FakeObservation(self.calls)

    def finish(self, **kwargs):  # noqa: ANN003
        return None


def test_langfuse_exporter_maps_span_kinds_and_swallows_errors() -> None:
    envelope = TraceEnvelope(
        trace_id="trace.langfuse",
        spans=[
            TraceSpan(trace_id="trace.langfuse", span_id="root", name="chat.request", kind="request"),
            TraceSpan(trace_id="trace.langfuse", span_id="tool", name="tool.kb.search", kind="tool"),
            TraceSpan(trace_id="trace.langfuse", span_id="llm", name="llm.answer", kind="llm"),
            TraceSpan(trace_id="trace.langfuse", span_id="node", name="node.rag", kind="node"),
        ],
    )
    run = FakeTraceRun()

    export_langfuse_trace_envelope(envelope, trace_context=object(), start_run=lambda _: run)
    export_langfuse_trace_envelope(envelope, trace_context=object(), start_run=lambda _: (_ for _ in ()).throw(RuntimeError("timeout")))

    assert ("tool.kb.search", "tool") in run.calls
    assert ("llm.answer", "generation") in run.calls
    assert ("node.rag", "span") in run.calls

