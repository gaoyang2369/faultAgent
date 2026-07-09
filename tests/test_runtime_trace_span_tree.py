from __future__ import annotations

from typing import Any

from fault_diagnosis.agent import ExecutionPlan, WorkflowRuntimeExecutor
from fault_diagnosis.agent.runtime import NodeExecutionOutput
from fault_diagnosis.platform.observability import TraceRecorder


class SkippedKg:
    node_type = "kg"

    def run(self, *, node: dict[str, Any], state):  # noqa: ANN001, ARG002
        return NodeExecutionOutput(status="skipped", output={"skipped_reason": "not_configured"})


def test_runtime_lifecycle_events_merge_into_node_spans() -> None:
    plan = ExecutionPlan(
        plan_id="plan.runtime.trace",
        plan_version="v2.test.validated",
        nodes=[
            {"node_id": "sql_1", "node_type": "sql"},
            {"node_id": "kg_1", "node_type": "kg"},
        ],
        edges=[{"from": "sql_1", "to": "kg_1"}],
    )
    result = WorkflowRuntimeExecutor(node_registry={"kg": SkippedKg()}).execute(plan, trace_id="trace.runtime.span")
    recorder = TraceRecorder(trace_id="trace.runtime.span", request_id="request.runtime.span", thread_id="thread.runtime.span")
    recorder.add_runtime_result(plan=plan, result=result)
    envelope = recorder.finish(status=result.status)

    node_spans = [span for span in envelope.spans if span.kind == "node"]
    kg_span = next(span for span in node_spans if span.name == "node.kg")

    assert [span.name for span in node_spans] == ["node.sql", "node.kg"]
    assert kg_span.status == "skipped"
    assert kg_span.attributes["reason"] == "not_configured"
    assert any(event.name == "node.running" for span in node_spans for event in span.events)
    assert envelope.metadata["executed_nodes"] == ["sql_1"]

