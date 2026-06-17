from __future__ import annotations

from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.workflow.todos import build_workflow_todos, summarize_workflow_todos


def test_workflow_todos_follow_enabled_nodes() -> None:
    decision = SingleAgentDecision(
        primary_task_type="alarm_triage",
        enabled_nodes={
            "sql": False,
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": False,
            "report": False,
        },
    )

    todos = build_workflow_todos(
        decision,
        completed_stages={"understand", "select_workflow_policy", "initialize_evidence_bundle"},
        current_stage="knowledge",
    )

    stages = [item["stage"] for item in todos]
    assert stages == ["plan", "evidence", "analysis", "validation"]
    assert len(todos) <= 5

    evidence_task = next(item for item in todos if item["stage"] == "evidence")
    assert evidence_task["status"] == "in_progress"
    assert evidence_task["stages"] == ["knowledge"]

    analysis_task = next(item for item in todos if item["stage"] == "analysis")
    assert analysis_task["stages"] == ["analysis", "resolution_recommendation"]


def test_workflow_todos_summary_marks_done_when_all_completed() -> None:
    decision = SingleAgentDecision(
        primary_task_type="status_query",
        enabled_nodes={"sql": True, "knowledge": False, "analysis": True, "report": False},
    )
    todos = build_workflow_todos(
        decision,
        completed_stages={
            "understand",
            "select_workflow_policy",
            "initialize_evidence_bundle",
            "sql",
            "analysis",
            "evidence_validation",
            "final_answer",
            "output_guardrail",
            "save_artifact",
        },
    )
    summary = summarize_workflow_todos(todos)

    assert [item["stage"] for item in todos] == ["plan", "evidence", "analysis", "validation"]
    assert summary["total"] == len(todos)
    assert summary["pending"] == 0
    assert summary["in_progress"] == 0
    assert summary["completed"] == len(todos)
