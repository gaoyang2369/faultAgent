from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, save_thread_artifact
from scripts.context_acceptance_test import artifact, build_client, login, plan


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def goal_types(snapshot: dict[str, Any]) -> list[str]:
    return list(snapshot.get("goal_set", {}).get("goal_types") or [])


def goals(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in snapshot.get("goals") or [] if isinstance(item, dict)]


def goal(snapshot: dict[str, Any], goal_type: str) -> dict[str, Any]:
    for item in goals(snapshot):
        if item.get("goal_type") == goal_type:
            return item
    raise AssertionError(f"missing goal {goal_type}")


def scenario_composite_question(client: TestClient) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(
        client,
        thread_id="goal.A",
        message="这个 A07089 是什么意思？现在设备有没有故障？应该怎么处理？",
    )
    expected = {"explain_fault_code", "check_runtime_status", "diagnose_fault", "recommend_resolution"}
    assert_true(snapshot["task_family"] == "diagnosis", "A must expose diagnosis task_family")
    assert_true(expected.issubset(set(goal_types(snapshot))), f"A missing goals: {expected - set(goal_types(snapshot))}")


def scenario_report_then_workorder(client: TestClient) -> None:
    thread_id = "goal.B"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="从结果来看是不是要生成工单？")
    context = snapshot["resolved_context"]
    workorder = goal(snapshot, "decide_workorder")
    assert_true(context["relation_to_previous"] == "action_followup", "B relation must be action_followup")
    assert_true(snapshot["task_family"] in {"diagnosis", "action_or_workorder"}, "B must expose stable task_family")
    assert_true("decide_workorder" in goal_types(snapshot), "B must include decide_workorder")
    assert_true(context["referenced_artifact_id"] in workorder.get("context_refs", []), "B workorder goal must reference previous artifact")


def scenario_stale_workorder_dependency(client: TestClient) -> None:
    thread_id = "goal.C"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", stale=True))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="要不要生成工单？")
    refresh = goal(snapshot, "refresh_current_status")
    workorder = goal(snapshot, "decide_workorder")
    assert_true({"refresh_current_status", "decide_workorder"}.issubset(set(goal_types(snapshot))), "C must include refresh and workorder")
    assert_true(workorder.get("depends_on") == [refresh["goal_id"]], "C workorder must depend on refresh goal_id")
    assert_true("refresh_current_status" not in workorder.get("depends_on", []), "C depends_on must not use goal_type")


def scenario_ambiguous_pronoun(client: TestClient) -> None:
    thread_id = "goal.D"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", created_at="2026-06-24T10:00:00"))
    save_thread_artifact(artifact(thread_id=thread_id, asset="J2", created_at="2026-06-24T10:01:00"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    clarify = goal(snapshot, "clarify_missing_context")
    severity = goal(snapshot, "assess_severity")
    assert_true(clarify["status"] == "ready", "D clarification must be ready")
    assert_true(severity["status"] == "blocked", "D severity must be blocked")


def scenario_explicit_device_switch(client: TestClient) -> None:
    thread_id = "goal.E"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="J2 当前状态怎么样")
    refs = [ref for item in goals(snapshot) for ref in item.get("context_refs", [])]
    assert_true(snapshot["task_family"] == "runtime_status", "E must expose runtime_status task_family")
    assert_true("eb_J1_A07089" not in refs, f"E must not reference J1 artifact: {refs}")


SCENARIOS: list[tuple[str, Callable[[TestClient], None]]] = [
    ("A composite_question", scenario_composite_question),
    ("B report_then_workorder", scenario_report_then_workorder),
    ("C stale_workorder_dependency", scenario_stale_workorder_dependency),
    ("D ambiguous_pronoun", scenario_ambiguous_pronoun),
    ("E explicit_device_switch", scenario_explicit_device_switch),
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
    if failed:
        print(json.dumps({"failed": failed}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"passed": len(SCENARIOS), "failed": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
