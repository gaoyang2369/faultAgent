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


def shadow(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("shadow_plan")
    assert_true(isinstance(value, dict) and value.get("planner_mode") == "shadow", "missing shadow planner summary")
    return value


def scenario_composite_question(client: TestClient) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="shadow.A", message="A07089 是什么，J1 现在是否还故障，怎么解决")
    summary = shadow(snapshot)
    assert_true({"knowledge", "sql", "analysis", "resolution_recommendation"}.issubset(set(summary["enabled_node_names"])), "A shadow nodes incomplete")


def scenario_report_handoff(client: TestClient) -> None:
    thread_id = "shadow.B"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="基于刚才结果生成报告")
    summary = shadow(snapshot)
    assert_true(summary["expected_output"] == "report", "B must expect report")
    assert_true("report" in summary["enabled_node_names"], "B must include report node")


def scenario_stale_workorder(client: TestClient) -> None:
    thread_id = "shadow.C"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", stale=True))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="从结果来看是不是要生成工单？")
    summary = shadow(snapshot)
    assert_true(summary["refresh_required"] is True, "C stale workorder must require refresh")
    assert_true(summary["expected_output"] == "workorder_decision", "C must expect workorder decision")


def scenario_ambiguous_reference(client: TestClient) -> None:
    thread_id = "shadow.D"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", created_at="2026-06-24T10:00:00"))
    save_thread_artifact(artifact(thread_id=thread_id, asset="J2", created_at="2026-06-24T10:01:00"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    summary = shadow(snapshot)
    assert_true(summary["expected_output"] == "clarification", "D must clarify ambiguous reference")
    assert_true(bool(summary["blocked_node_names"]), "D must block business nodes")


def scenario_explicit_device_switch(client: TestClient) -> None:
    thread_id = "shadow.E"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="J2 当前状态怎么样")
    summary = shadow(snapshot)
    assert_true(snapshot["resolved_context"]["inherited_slots"].get("device") != "J1", "E must not inherit J1")
    assert_true("sql" in summary["enabled_node_names"], "E must include SQL for switched device")


SCENARIOS: list[tuple[str, Callable[[TestClient], None]]] = [
    ("A composite_question", scenario_composite_question),
    ("B report_handoff", scenario_report_handoff),
    ("C stale_workorder", scenario_stale_workorder),
    ("D ambiguous_reference", scenario_ambiguous_reference),
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
