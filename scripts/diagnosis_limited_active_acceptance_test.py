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


def set_gate() -> None:
    config.ENABLE_PLANNER_GATED_EXECUTION = True
    config.PLANNER_GATED_DRY_RUN = False
    config.PLANNER_GATE_DIAGNOSIS_DRY_RUN = True
    config.PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE = True
    config.PLANNER_GATE_DIAGNOSIS_ACTIVE_MODES = ["alarm_triage", "fault_diagnosis"]
    config.PLANNER_GATE_DIAGNOSIS_ACTIVE_REQUIRE_READINESS = "candidate_for_limited_active"
    config.PLANNER_GATE_DIAGNOSIS_ACTIVE_MAX_DIFF_SEVERITY = "warning"
    config.PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_RCA = False
    config.PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_HEALTH = False


def decision(
    *,
    primary_task_type: str = "alarm_triage",
    goals: list[dict[str, Any]] | None = None,
    enabled_nodes: dict[str, bool] | None = None,
    runtime_tools: list[str] | None = None,
    resolved_context: dict[str, Any] | None = None,
) -> SingleAgentDecision:
    goals = goals or [{"goal_type": "check_runtime_status", "risk_level": "read_only"}]
    return SingleAgentDecision(
        primary_task_type=primary_task_type,
        task_family="diagnosis",
        enabled_nodes=enabled_nodes
        or {
            "sql": True,
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": True,
            "evidence_validation": True,
            "output_guardrail": True,
        },
        runtime_tools=runtime_tools or ["sql_db_query_checker", "sql_db_query", "query_knowledge_base"],
        objects={"device_ids": ["J1"], "alarm_codes": ["A07089"]},
        intent_stack=["explain_alarm_code", "check_current_status", "resolution_recommendation"],
        goals=goals,
        goal_set={"goals": goals},
        resolved_context=resolved_context or {"relation_to_previous": "new_case", "stale_evidence": False},
        authorization={"mode": "allow"},
    )


def shadow(
    *,
    nodes: list[str] | None = None,
    tools: list[str] | None = None,
    missing_evidence: list[str] | None = None,
    disclosures: list[str] | None = None,
) -> dict[str, Any]:
    nodes = nodes or ["sql", "knowledge", "analysis", "resolution_recommendation", "evidence_validation", "output_guardrail"]
    tools = tools or ["sql_db_query", "query_knowledge_base"]
    return {
        "nodes": [{"node": node, "desired_state": "enabled"} for node in nodes],
        "tool_plan": {"candidate_tools": tools, "authorized_runtime_tools": tools},
        "evidence_plan": {
            "required_evidence": ["runtime_data", "knowledge_source", "diagnosis_basis"],
            "missing_evidence": missing_evidence or [],
            "refresh_required": False,
            "disclosure_required": disclosures or [],
        },
        "output_plan": {"expected_output": "answer", "required_disclosures": disclosures or []},
    }


def gate(
    dec: SingleAgentDecision,
    shadow_plan: dict[str, Any] | None = None,
    *,
    diff: dict[str, Any] | None = None,
) -> Any:
    return build_planner_gate(
        decision=dec,
        shadow_plan=shadow_plan or shadow(),
        planning_diff=diff or {"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False, "diagnosis_active": True, "diagnosis_dry_run": True},
    )


def readiness(gate_value: Any) -> dict[str, Any]:
    value = gate_value.safety_summary.get("diagnosis_readiness")
    assert_true(isinstance(value, dict) and value, "missing diagnosis_readiness")
    return value


def assert_active_safe(gate_value: Any, dec: SingleAgentDecision) -> None:
    assert_true(gate_value.selected_execution_source == "planner_gated", "expected planner_gated")
    assert_true(set(gate_value.final_runtime_tools).issubset(set(dec.runtime_tools)), "runtime tools must not expand")
    assert_true("workorder_decision" not in gate_value.final_enabled_nodes, "workorder_decision must not be active")
    assert_true("evidence_validation" in gate_value.final_enabled_nodes, "evidence_validation must be preserved")
    assert_true("output_guardrail" in gate_value.final_enabled_nodes, "output_guardrail must be preserved")
    assert_true(readiness(gate_value)["active_allowed"] is True, "readiness must allow limited active")


def assert_blocked(gate_value: Any, blocker: str) -> None:
    assert_true(gate_value.selected_execution_source == "legacy_policy", "expected legacy fallback")
    assert_true(blocker in gate_value.blockers, f"missing blocker {blocker}")


def scenario_alarm_triage_active(_: TestClient, stats: list[dict[str, Any]]) -> None:
    dec = decision(primary_task_type="alarm_triage")
    result = gate(dec)
    assert_active_safe(result, dec)
    stats.append(readiness(result))


def scenario_fault_diagnosis_active(_: TestClient, stats: list[dict[str, Any]]) -> None:
    dec = decision(primary_task_type="fault_diagnosis", goals=[{"goal_type": "diagnose_fault", "risk_level": "read_only"}])
    result = gate(dec)
    assert_active_safe(result, dec)
    assert_true(set(result.active_scope) == {"sql", "knowledge", "analysis", "resolution_recommendation"}, "unexpected active scope")
    stats.append(readiness(result))


def scenario_rca_blocked(_: TestClient, stats: list[dict[str, Any]]) -> None:
    result = gate(decision(primary_task_type="root_cause_analysis"))
    assert_blocked(result, "root_cause_analysis_not_migrated")
    stats.append(readiness(result))


def scenario_health_blocked(_: TestClient, stats: list[dict[str, Any]]) -> None:
    result = gate(decision(primary_task_type="health_assessment"))
    assert_blocked(result, "health_assessment_not_migrated")
    stats.append(readiness(result))


def scenario_stale_blocked(_: TestClient, stats: list[dict[str, Any]]) -> None:
    result = gate(decision(resolved_context={"relation_to_previous": "continuation", "stale_evidence": True}), shadow(disclosures=[]))
    assert_blocked(result, "stale_evidence_without_disclosure")
    stats.append(readiness(result))


def scenario_action_workorder_blocked(_: TestClient, stats: list[dict[str, Any]]) -> None:
    result = gate(decision(goals=[{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}]))
    assert_blocked(result, "action_or_workorder_not_migrated")
    stats.append(readiness(result))


def scenario_ambiguous_blocked(client: TestClient, stats: list[dict[str, Any]]) -> None:
    thread_id = "diagnosis-limited-active.G"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    save_thread_artifact(artifact(thread_id=thread_id, asset="J2"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    gate_summary = snapshot["planner_gate"]
    assert_true(gate_summary["selected_execution_source"] == "legacy_policy", "ambiguous must fallback")
    assert_true("blocked_context_relation:ambiguous" in gate_summary["blockers"], "ambiguous blocker missing")
    stats.append(snapshot["diagnosis_readiness"])


def scenario_unauthorized_blocked(client: TestClient, stats: list[dict[str, Any]]) -> None:
    thread_id = "diagnosis-limited-active.H"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="guest")
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    text = json.dumps(snapshot["diagnosis_readiness"], ensure_ascii=False)
    assert_true(snapshot["planner_gate"]["selected_execution_source"] == "legacy_policy", "unauthorized must fallback")
    assert_true("unauthorized_inherited_artifact" in snapshot["planner_gate"]["blockers"], "unauthorized blocker missing")
    assert_true("J1" not in text and "A07089" not in text, "readiness must not leak unauthorized details")
    stats.append(snapshot["diagnosis_readiness"])


def scenario_critical_diff_blocked(_: TestClient, stats: list[dict[str, Any]]) -> None:
    result = gate(
        decision(),
        diff={"overall_status": "unsafe_mismatch", "severity": "critical", "counters": {"critical_count": 1}},
    )
    assert_blocked(result, "critical_diff_present")
    stats.append(readiness(result))


def scenario_tool_scope_violation_blocked(_: TestClient, stats: list[dict[str, Any]]) -> None:
    result = gate(decision(), shadow(tools=["sql_db_query", "query_knowledge_base", "save_report"]))
    assert_blocked(result, "tool_scope_violation")
    stats.append(readiness(result))


SCENARIOS: list[tuple[str, Callable[[TestClient, list[dict[str, Any]]], None]]] = [
    ("A alarm_triage_active", scenario_alarm_triage_active),
    ("B fault_diagnosis_active", scenario_fault_diagnosis_active),
    ("C rca_blocked", scenario_rca_blocked),
    ("D health_blocked", scenario_health_blocked),
    ("E stale_blocked", scenario_stale_blocked),
    ("F action_workorder_blocked", scenario_action_workorder_blocked),
    ("G ambiguous_blocked", scenario_ambiguous_blocked),
    ("H unauthorized_blocked", scenario_unauthorized_blocked),
    ("I critical_diff_blocked", scenario_critical_diff_blocked),
    ("J tool_scope_violation_blocked", scenario_tool_scope_violation_blocked),
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
    active_allowed = [item for item in stats if item.get("active_allowed")]
    summary = {
        "passed": len(SCENARIOS) - len(failed),
        "failed": failed,
        "active_allowed_count": len(active_allowed),
        "active_modes": [item.get("diagnosis_mode") for item in active_allowed],
        "ready_for_active_count": sum(1 for item in stats if item.get("ready_for_active")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
