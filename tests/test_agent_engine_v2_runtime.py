from __future__ import annotations

from typing import Any

from fault_diagnosis.agent_engine import (
    CancelToken,
    ExecutionPlan,
    RuntimeResult,
    RuntimeState,
    WorkflowRuntimeExecutor,
)
from fault_diagnosis.agent_engine.runtime import NodeExecutionOutput


def _plan(
    *,
    plan_id: str = "plan.validated",
    plan_version: str = "v2.candidate.phase4.validated",
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    approval_requirements: list[dict[str, Any]] | None = None,
    interrupts: list[dict[str, Any]] | None = None,
) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=plan_id,
        plan_version=plan_version,
        nodes=nodes
        or [
            {
                "node_id": "sql_1",
                "node_type": "sql",
                "inputs": {"device_refs": ["J1"]},
                "required_tools": ["sql.read"],
            }
        ],
        edges=edges or [],
        allowed_tools=["sql.read"],
        required_evidence=["latest_runtime_status"],
        approval_requirements=approval_requirements or [],
        interrupts=interrupts or [],
    )


def test_runtime_executes_validated_runtime_status_plan_with_fake_sql_node() -> None:
    result = WorkflowRuntimeExecutor().execute(
        _plan(),
        trace_id="trace.runtime",
        thread_id="thread.runtime",
        request_id="request.runtime",
    )

    assert isinstance(result, RuntimeResult)
    assert result.status == "completed"
    assert [node.status for node in result.node_results] == ["completed"]
    assert result.node_results[0].node_type == "sql"
    assert result.node_results[0].evidence_refs == ["fake_ev_sql_1"]
    assert result.evidence_ledger.evidence_items[0]["evidence_id"] == "fake_ev_sql_1"
    assert result.complete_payload["type"] == "chat_complete"
    assert result.complete_payload["runtime"] == "agent_engine_v2"


def test_runtime_rejects_unvalidated_candidate_plan_without_node_execution() -> None:
    result = WorkflowRuntimeExecutor().execute(
        _plan(plan_version="v2.candidate.phase4"),
        trace_id="trace.runtime",
    )

    assert result.status == "blocked"
    assert result.node_results == []
    assert result.evidence_ledger.evidence_items == []
    assert result.complete_payload["status"] == "blocked"
    assert result.trace["errors"][0]["code"] == "validated_plan_required"

    blocked_result = WorkflowRuntimeExecutor().execute(_plan(plan_version="v2.candidate.phase4.validated.blocked"))
    assert blocked_result.status == "blocked"
    assert blocked_result.node_results == []


def test_runtime_records_pending_running_completed_and_skipped_lifecycle() -> None:
    plan = _plan(
        nodes=[
            {"node_id": "sql_1", "node_type": "sql"},
            {"node_id": "unknown_1", "node_type": "unknown_fake"},
        ],
        edges=[{"from": "sql_1", "to": "unknown_1"}],
    )

    result = WorkflowRuntimeExecutor().execute(plan)
    statuses = [(item.node_id, item.status) for item in result.node_results]
    trace_statuses = [event["status"] for event in result.trace["events"] if event["event_type"] == "node_status"]

    assert result.status == "completed"
    assert statuses == [("sql_1", "completed"), ("unknown_1", "skipped")]
    assert "pending" in trace_statuses
    assert "running" in trace_statuses
    assert "completed" in trace_statuses
    assert "skipped" in trace_statuses


def test_runtime_retries_fake_node_failure_and_does_not_pollute_evidence_ledger() -> None:
    class FailingNode:
        node_type = "fail"

        def __init__(self) -> None:
            self.calls = 0

        def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:  # noqa: ARG002
            self.calls += 1
            raise RuntimeError("fake failure")

    failing = FailingNode()
    plan = _plan(nodes=[{"node_id": "fail_1", "node_type": "fail", "retry": {"max_attempts": 2}}])

    result = WorkflowRuntimeExecutor(node_registry={"fail": failing}).execute(plan)

    assert result.status == "failed"
    assert failing.calls == 2
    assert result.node_results[0].status == "failed"
    assert result.node_results[0].retry_count == 1
    assert result.node_results[0].error["code"] == "RuntimeError"
    assert result.evidence_ledger.evidence_items == []
    assert any(event["event_type"] == "node_retry" for event in result.trace["events"])


def test_runtime_blocks_workorder_or_approval_node_at_approval_boundary() -> None:
    plan = _plan(
        nodes=[{"node_id": "workorder_1", "node_type": "workorder"}],
        approval_requirements=[
            {
                "requirement_id": "approval_workorder_draft",
                "type": "workorder_draft",
                "required": True,
                "required_role": "engineer",
                "allowed_next_step": "draft_only",
            }
        ],
        interrupts=[{"interrupt_id": "interrupt_approval_workorder_draft", "type": "approval_required"}],
    )

    result = WorkflowRuntimeExecutor().execute(plan)

    assert result.status == "blocked"
    assert result.node_results[0].status == "blocked"
    assert result.node_results[0].error["code"] == "approval_required"
    assert result.trace["interrupts"]
    assert result.evidence_ledger.evidence_items == []


def test_runtime_cancel_token_stops_execution_and_returns_compatible_cancel_payload() -> None:
    class CancellingNode:
        node_type = "cancel"

        def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:  # noqa: ARG002
            state.cancel_token.cancel("user_stop")
            return NodeExecutionOutput(output={"status": "should_not_commit"})

    plan = _plan(
        nodes=[
            {"node_id": "cancel_1", "node_type": "cancel"},
            {"node_id": "sql_2", "node_type": "sql"},
        ],
        edges=[{"from": "cancel_1", "to": "sql_2"}],
    )

    result = WorkflowRuntimeExecutor(node_registry={"cancel": CancellingNode()}).execute(
        plan,
        cancel_token=CancelToken(),
        trace_id="trace.cancel",
        thread_id="thread.cancel",
    )

    assert result.status == "cancelled"
    assert [item.status for item in result.node_results] == ["cancelled", "cancelled"]
    assert result.cancel_payload is not None
    assert result.cancel_payload["type"] == "chat_complete"
    assert result.cancel_payload["cancelled"] is True
    assert result.cancel_payload["cancel_reason"] == "user_stop"
    assert result.cancel_payload["final_content"] == ""
    assert result.cancel_payload["todos"] == []
    assert result.evidence_ledger.evidence_items == []


def test_runtime_trace_reconstructs_node_order_summaries_duration_and_error() -> None:
    class OneShotFailure:
        node_type = "fail"

        def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:  # noqa: ARG002
            raise ValueError("traceable failure")

    plan = _plan(nodes=[{"node_id": "fail_1", "node_type": "fail", "inputs": {"x": 1}}])

    result = WorkflowRuntimeExecutor(node_registry={"fail": OneShotFailure()}).execute(plan)

    assert result.trace["node_order"] == ["fail_1"]
    final_event = [
        event
        for event in result.trace["events"]
        if event["event_type"] == "node_status" and event["status"] == "failed"
    ][-1]
    assert final_event["node_id"] == "fail_1"
    assert "inputs" in final_event["input_summary"]
    assert final_event["duration_ms"] >= 0
    assert final_event["retry_count"] == 0
    assert final_event["error"]["code"] == "ValueError"
