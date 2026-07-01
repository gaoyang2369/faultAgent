from __future__ import annotations

import json
import sys
from collections import Counter
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


def set_gate() -> None:
    config.ENABLE_PLANNER_GATED_EXECUTION = True
    config.PLANNER_GATED_DRY_RUN = False
    config.PLANNER_GATE_DIAGNOSIS_DRY_RUN = True
    config.PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE = False
    config.PLANNER_GATED_TASK_FAMILIES = ["knowledge_lookup", "runtime_status", "reporting"]
    config.PLANNER_GATED_REQUIRE_DIFF_STATUS = ["aligned", "acceptable_diff"]
    config.PLANNER_GATED_MAX_DIFF_SEVERITY = "warning"


def planner_gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    gate = snapshot.get("planner_gate")
    assert_true(isinstance(gate, dict) and gate, "missing planner_gate")
    return gate


def readiness(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("diagnosis_readiness") or (planner_gate(snapshot)).get("diagnosis_readiness")
    assert_true(isinstance(value, dict) and value, "missing diagnosis_readiness")
    return value


def assert_execution_unchanged(snapshot: dict[str, Any]) -> None:
    gate = planner_gate(snapshot)
    enabled = sorted(node for node, value in (snapshot.get("enabled_nodes") or {}).items() if value)
    assert_true(gate["selected_execution_source"] == "legacy_policy", "diagnosis must stay legacy_policy")
    assert_true(sorted(gate["final_enabled_nodes"]) == enabled, "enabled_nodes must stay unchanged")
    assert_true(gate["final_runtime_tools"] == snapshot.get("planned_tools", []), "runtime_tools must stay unchanged")


def scenario_alarm_triage(client: TestClient, stats: list[dict[str, Any]]) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="diagnosis-dry-run.A", message="J1 的 A07089 现在还在报警吗，怎么处理")
    assert_true(snapshot["task_family"] == "diagnosis", "A must be diagnosis family")
    assert_execution_unchanged(snapshot)
    ready = readiness(snapshot)
    assert_true(ready["diagnosis_mode"] == "alarm_triage", "A must be alarm_triage")
    assert_true(ready["ready_for_active"] is False, "A ready_for_active must stay false")
    stats.append(ready)


def scenario_fault_diagnosis(client: TestClient, stats: list[dict[str, Any]]) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="diagnosis-dry-run.B", message="诊断 J1 A07089 的原因")
    assert_execution_unchanged(snapshot)
    ready = readiness(snapshot)
    assert_true(ready["diagnosis_mode"] == "fault_diagnosis", "B must be fault_diagnosis")
    assert_true(ready["ready_for_active"] is False, "B ready_for_active must stay false")
    stats.append(ready)


def scenario_rca(client: TestClient, stats: list[dict[str, Any]]) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="diagnosis-dry-run.C", message="对 J1 A07089 做根因分析")
    assert_execution_unchanged(snapshot)
    ready = readiness(snapshot)
    assert_true(ready["diagnosis_mode"] == "root_cause_analysis", "C must be RCA")
    assert_true(ready["recommended_next_phase"] in {"keep_legacy", "more_eval"}, "C must stay conservative")
    stats.append(ready)


def scenario_health(client: TestClient, stats: list[dict[str, Any]]) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="diagnosis-dry-run.D", message="评估 J1 最近健康风险")
    assert_execution_unchanged(snapshot)
    ready = readiness(snapshot)
    assert_true(ready["diagnosis_mode"] == "health_assessment", "D must be health_assessment")
    assert_true(ready["recommended_next_phase"] in {"keep_legacy", "more_eval"}, "D must stay conservative")
    stats.append(ready)


def scenario_stale_without_disclosure(_: TestClient, stats: list[dict[str, Any]]) -> None:
    decision = SingleAgentDecision(
        primary_task_type="fault_diagnosis",
        task_family="diagnosis",
        enabled_nodes={"sql": True, "knowledge": True, "analysis": True},
        runtime_tools=["sql_db_query", "query_knowledge_base"],
        objects={"device_ids": ["J1"], "alarm_codes": ["A07089"]},
        intent_stack=["fault_diagnosis"],
        resolved_context={"relation_to_previous": "continuation", "stale_evidence": True},
        authorization={"mode": "allow"},
    )
    shadow = {
        "nodes": [{"node": node, "desired_state": "enabled"} for node in ["sql", "knowledge", "analysis"]],
        "tool_plan": {"authorized_runtime_tools": ["sql_db_query", "query_knowledge_base"]},
        "evidence_plan": {"required_evidence": ["runtime_data", "knowledge_source", "diagnosis_basis"]},
        "output_plan": {"expected_output": "answer", "required_disclosures": []},
    }
    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )
    ready = gate.safety_summary["diagnosis_readiness"]
    assert_true("stale_evidence_without_disclosure" in ready["blocked_reasons"], "E must block stale without disclosure")
    assert_true(gate.selected_execution_source == "legacy_policy", "E must stay legacy")
    stats.append(ready)


def scenario_ambiguous_context(client: TestClient, stats: list[dict[str, Any]]) -> None:
    thread_id = "diagnosis-dry-run.F"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    save_thread_artifact(artifact(thread_id=thread_id, asset="J2"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    assert_execution_unchanged(snapshot)
    ready = readiness(snapshot)
    assert_true(ready["ready_for_active"] is False, "F ready_for_active must stay false")
    assert_true(bool(planner_gate(snapshot)["blockers"]), "F must be blocked")
    stats.append(ready)


def scenario_unauthorized_inheritance(client: TestClient, stats: list[dict[str, Any]]) -> None:
    thread_id = "diagnosis-dry-run.G"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="guest")
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    text = json.dumps(snapshot.get("diagnosis_readiness") or {}, ensure_ascii=False)
    assert_execution_unchanged(snapshot)
    assert_true("J1" not in text and "A07089" not in text, "G readiness must not leak details")
    ready = readiness(snapshot)
    assert_true(bool(planner_gate(snapshot)["blockers"]), "G must be blocked")
    stats.append(ready)


def scenario_action_workorder(_: TestClient, stats: list[dict[str, Any]]) -> None:
    decision = SingleAgentDecision(
        primary_task_type="action_request",
        task_family="action_or_workorder",
        enabled_nodes={"permission_check": True, "risk_check": True, "workorder_decision": True},
        runtime_tools=[],
        goals=[{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}],
        goal_set={"goals": [{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}]},
        resolved_context={"relation_to_previous": "action_followup"},
        authorization={"mode": "allow"},
    )
    gate = build_planner_gate(
        decision=decision,
        shadow_plan={"nodes": [], "tool_plan": {"authorized_runtime_tools": []}, "output_plan": {"expected_output": "workorder_decision"}},
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )
    assert_true(gate.selected_execution_source == "legacy_policy", "H must stay legacy")
    assert_true("action_or_workorder_not_migrated" in gate.blockers, "H must block action/workorder")
    assert_true("diagnosis_readiness" not in gate.safety_summary, "H must not create diagnosis active readiness")
    stats.append({"diagnosis_mode": "action_or_workorder", "recommended_next_phase": "keep_legacy", "ready_for_active": False})


SCENARIOS: list[tuple[str, Callable[[TestClient, list[dict[str, Any]]], None]]] = [
    ("A alarm_triage", scenario_alarm_triage),
    ("B fault_diagnosis", scenario_fault_diagnosis),
    ("C rca", scenario_rca),
    ("D health", scenario_health),
    ("E stale_without_disclosure", scenario_stale_without_disclosure),
    ("F ambiguous_context", scenario_ambiguous_context),
    ("G unauthorized_inheritance", scenario_unauthorized_inheritance),
    ("H action_workorder", scenario_action_workorder),
]


def main() -> int:
    set_gate()
    client = build_client()
    failed: list[str] = []
    stats: list[dict[str, Any]] = []
    with client:
        for name, func in SCENARIOS:
            clear_all_artifacts()
            try:
                func(client, stats)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                print(f"FAIL {name}: {exc}")
            else:
                print(f"PASS {name}")
    mode_counts = Counter(str(item.get("diagnosis_mode") or "unknown") for item in stats)
    next_counts = Counter(str(item.get("recommended_next_phase") or "keep_legacy") for item in stats)
    summary = {
        "passed": len(SCENARIOS) - len(failed),
        "failed": failed,
        "readiness_by_mode": dict(mode_counts),
        "recommended_next_phase": dict(next_counts),
        "candidate_modes": sorted(
            {
                str(item.get("diagnosis_mode"))
                for item in stats
                if item.get("recommended_next_phase") == "candidate_for_limited_active"
            }
        ),
        "ready_for_active_count": sum(1 for item in stats if item.get("ready_for_active")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
