from __future__ import annotations

from fault_diagnosis.agent import AgentEngineV2, WorkflowRuntimeExecutor
from fault_diagnosis.agent.evidence import EvidenceLedgerWriter, create_ledger
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.observability import TraceRecorder
from tests.evals.canonical_output_regressions import assert_regressions, run_regressions


def test_canonical_output_regressions() -> None:
    assert_regressions(run_regressions())


def test_canonical_trace_contains_goal_and_composition_spans() -> None:
    auth = build_auth_context(role="admin")
    snapshot = AgentEngineV2().build_plan_snapshot(raw_message="A07089 是什么意思", auth_context=auth)
    result = WorkflowRuntimeExecutor().execute(
        snapshot.execution_plan,
        trace_id="trace.phase4.spans",
        thread_id="thread.phase4.spans",
        request_id="request.phase4.spans",
        auth_context=auth,
    )
    recorder = TraceRecorder(
        trace_id="trace.phase4.spans",
        request_id="request.phase4.spans",
        thread_id="thread.phase4.spans",
    )
    recorder.add_plan_snapshot(snapshot)
    recorder.add_runtime_result(plan=snapshot.execution_plan, result=result)
    trace = recorder.finish(status=result.status)
    names = {span.name for span in trace.spans}

    assert {
        "canonical.request",
        "canonical.goal",
        "goal.authorization",
        "goal.readiness",
        "goal.source_resolution",
        "goal.plan",
        "goal.execution",
        "goal.artifact",
        "goal.evidence",
        "goal.deliverable",
        "answer.composition",
    } <= names


def test_deduped_evidence_retains_all_goal_owners() -> None:
    ledger = create_ledger(trace_id="trace.phase4.evidence")
    writer = EvidenceLedgerWriter(ledger)
    evidence = [{"evidence_id": "ev_shared", "summary": "共享事实", "metadata": {"authorized": True}}]

    writer.commit_evidence(evidence, node={"node_id": "sql_1", "node_type": "sql", "goal_ids": ["g1"]})
    writer.commit_evidence(evidence, node={"node_id": "sql_2", "node_type": "sql", "goal_ids": ["g2"]})

    assert ledger.evidence_items[0]["goal_ids"] == ["g1", "g2"]
    assert ledger.evidence_items[0]["producer_goal_id"] is None
