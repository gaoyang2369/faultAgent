from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_diagnosis import config
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, save_thread_artifact
from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.planning import build_planner_gate
from scripts.context_acceptance_test import artifact, build_client, login, plan


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def set_gate(*, enabled: bool, dry_run: bool) -> None:
    config.ENABLE_PLANNER_GATED_EXECUTION = enabled
    config.PLANNER_GATED_DRY_RUN = dry_run
    config.PLANNER_GATED_TASK_FAMILIES = ["knowledge_lookup", "runtime_status", "reporting"]
    config.PLANNER_GATED_REQUIRE_DIFF_STATUS = ["aligned", "acceptable_diff"]
    config.PLANNER_GATED_MAX_DIFF_SEVERITY = "warning"


def gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("planner_gate")
    assert_true(isinstance(value, dict) and value, "missing planner_gate summary")
    return value


def scenario_default_disabled(client: TestClient) -> None:
    set_gate(enabled=False, dry_run=True)
    login(client, role="guest")
    snapshot = plan(client, thread_id="planner-gate.A", message="A07089 是什么意思")
    summary = gate(snapshot)
    assert_true(summary["mode"] == "disabled", "A gate must be disabled by default")
    assert_true(summary["selected_execution_source"] == "legacy_policy", "A must use legacy policy")
    assert_true("query_knowledge_base" in snapshot["planned_tools"], "A legacy tools must remain")


def scenario_dry_run_eligible(client: TestClient) -> None:
    set_gate(enabled=True, dry_run=True)
    login(client, role="guest")
    snapshot = plan(client, thread_id="planner-gate.B", message="A07089 是什么意思")
    summary = gate(snapshot)
    assert_true(summary["mode"] == "dry_run", "B must be dry_run")
    assert_true(summary["eligible"] is True, "B should be eligible in dry_run")
    assert_true(summary["selected_execution_source"] == "legacy_policy", "B dry_run must not switch execution")


def scenario_knowledge_active(client: TestClient) -> None:
    set_gate(enabled=True, dry_run=False)
    login(client, role="guest")
    snapshot = plan(client, thread_id="planner-gate.C", message="A07089 是什么意思")
    summary = gate(snapshot)
    assert_true(summary["selected_execution_source"] == "planner_gated", "C should use planner-gated preview")
    assert_true(summary["final_enabled_nodes"] == ["knowledge"], "C must only enable knowledge")
    assert_true(summary["final_runtime_tools"] == ["query_knowledge_base"], "C must only allow KB tool")


def scenario_runtime_status_active(client: TestClient) -> None:
    set_gate(enabled=True, dry_run=False)
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="planner-gate.D", message="J1 当前状态怎么样")
    summary = gate(snapshot)
    assert_true(summary["selected_execution_source"] == "planner_gated", "D should use planner-gated preview")
    assert_true(summary["final_enabled_nodes"] == ["sql"], "D must only enable SQL")
    assert_true(set(summary["final_runtime_tools"]).issubset({"sql_db_query_checker", "sql_db_query"}), "D must only allow SQL tools")
    assert_true("save_report" not in summary["final_runtime_tools"], "D must not allow report")


def scenario_report_handoff_active(client: TestClient) -> None:
    set_gate(enabled=True, dry_run=False)
    thread_id = "planner-gate.E"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="基于刚才结果生成报告")
    summary = gate(snapshot)
    assert_true(summary["selected_execution_source"] == "planner_gated", "E should use planner-gated preview")
    assert_true(summary["final_enabled_nodes"] == ["report"], "E must only enable report")
    assert_true(summary["final_runtime_tools"] == ["save_report"], "E must only allow report tool")


def scenario_diagnosis_blocked(client: TestClient) -> None:
    set_gate(enabled=True, dry_run=False)
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="planner-gate.F", message="诊断 J1 A07089 的原因")
    summary = gate(snapshot)
    assert_true(summary["selected_execution_source"] == "legacy_policy", "F diagnosis must stay legacy")
    assert_true("unsupported_task_family" in summary["blockers"], "F must block unsupported family")


def scenario_action_blocked(client: TestClient) -> None:
    set_gate(enabled=True, dry_run=False)
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="planner-gate.G", message="帮我重启 J1")
    summary = gate(snapshot)
    assert_true(summary["selected_execution_source"] == "legacy_policy", "G action must stay legacy")
    assert_true("action_or_workorder_not_migrated" in summary["blockers"], "G must block action/workorder")


def scenario_needs_review_blocked(client: TestClient) -> None:
    set_gate(enabled=True, dry_run=False)
    thread_id = "planner-gate.H"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="guest")
    snapshot = plan(client, thread_id=thread_id, message="它要不要生成工单？")
    summary = gate(snapshot)
    assert_true(summary["selected_execution_source"] == "legacy_policy", "H needs_review must stay legacy")
    assert_true(summary["blockers"], "H must expose blockers")


def scenario_critical_blocked(_: TestClient) -> None:
    set_gate(enabled=True, dry_run=False)
    decision = SingleAgentDecision(
        task_family="knowledge_lookup",
        primary_task_type="knowledge_qa",
        enabled_nodes={"knowledge": True},
        runtime_tools=["query_knowledge_base"],
        goal_set={"goals": [{"goal_type": "explain_fault_code", "risk_level": "read_only"}]},
        authorization={"mode": "allow"},
        resolved_context={"relation_to_previous": "new_case"},
    )
    shadow = {
        "nodes": [{"node": "knowledge", "desired_state": "enabled"}],
        "tool_plan": {"authorized_runtime_tools": ["query_knowledge_base"]},
        "output_plan": {"expected_output": "answer"},
    }
    summary = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "unsafe_mismatch", "severity": "critical", "counters": {"critical_count": 1}},
    )
    assert_true(summary.selected_execution_source == "legacy_policy", "I critical diff must stay legacy")
    assert_true("critical_diff_present" in summary.blockers, "I must block critical diff")


SCENARIOS: list[tuple[str, Callable[[TestClient], None]]] = [
    ("A default_disabled", scenario_default_disabled),
    ("B dry_run_eligible", scenario_dry_run_eligible),
    ("C knowledge_active", scenario_knowledge_active),
    ("D runtime_status_active", scenario_runtime_status_active),
    ("E report_handoff_active", scenario_report_handoff_active),
    ("F diagnosis_blocked", scenario_diagnosis_blocked),
    ("G action_blocked", scenario_action_blocked),
    ("H needs_review_blocked", scenario_needs_review_blocked),
    ("I critical_blocked", scenario_critical_blocked),
]


def main() -> int:
    client = build_client()
    failed: list[str] = []
    with client:
        for name, func in SCENARIOS:
            clear_all_artifacts()
            try:
                func(client)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                print(f"FAIL {name}: {exc}")
            else:
                print(f"PASS {name}")
    set_gate(enabled=False, dry_run=True)
    print(json.dumps({"passed": len(SCENARIOS) - len(failed), "failed": failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
