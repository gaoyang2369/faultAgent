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


def planning_diff(snapshot: dict[str, Any]) -> dict[str, Any]:
    diff = snapshot.get("planning_diff")
    assert_true(isinstance(diff, dict) and diff, "missing planning_diff compact summary")
    assert_true("node_diffs" not in diff and "tool_diffs" not in diff, "full planning_diff leaked into compact summary")
    assert_true((diff.get("migration_readiness") or {}).get("safe_to_migrate") is False, "Phase 4.2 must not mark migration safe")
    return diff


def assert_no_critical(snapshot: dict[str, Any]) -> None:
    diff = planning_diff(snapshot)
    assert_true(int(diff.get("critical_count") or 0) == 0, f"planning_diff has critical mismatch: {diff}")


def assert_shadow_tools_subset_legacy(snapshot: dict[str, Any]) -> None:
    legacy = set(snapshot.get("planned_tools") or [])
    shadow = set((snapshot.get("shadow_plan") or {}).get("authorized_runtime_tools") or [])
    assert_true(shadow.issubset(legacy), f"shadow authorized tools exceed legacy runtime_tools: {shadow - legacy}")


def assert_no_sensitive_leak(snapshot: dict[str, Any], forbidden: list[str]) -> None:
    text = json.dumps(snapshot.get("planning_diff") or {}, ensure_ascii=False)
    for item in forbidden:
        assert_true(item not in text, f"planning_diff leaked forbidden detail: {item}")


def scenario_knowledge_lookup(client: TestClient) -> dict[str, Any]:
    login(client, role="guest")
    snapshot = plan(client, thread_id="planning-diff.A", message="A07089 是什么意思")
    assert_no_critical(snapshot)
    assert_shadow_tools_subset_legacy(snapshot)
    assert_true(snapshot["enabled_nodes"].get("knowledge") is True, "knowledge node must remain enabled")
    assert_true("query_knowledge_base" in snapshot["planned_tools"], "legacy runtime_tools must keep KB tool")
    return snapshot


def scenario_runtime_status(client: TestClient) -> dict[str, Any]:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="planning-diff.B", message="J1 当前状态怎么样")
    assert_no_critical(snapshot)
    assert_shadow_tools_subset_legacy(snapshot)
    assert_true(snapshot["enabled_nodes"].get("sql") is True, "sql node must remain enabled")
    assert_true("sql_db_query" in snapshot["planned_tools"], "legacy runtime_tools must keep SQL tool")
    return snapshot


def scenario_report_handoff(client: TestClient) -> dict[str, Any]:
    thread_id = "planning-diff.C"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="基于刚才结果生成报告")
    assert_no_critical(snapshot)
    assert_shadow_tools_subset_legacy(snapshot)
    assert_true(snapshot["enabled_nodes"].get("report") is True, "report node must remain enabled")
    return snapshot


def scenario_stale_workorder(client: TestClient) -> dict[str, Any]:
    thread_id = "planning-diff.D"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", stale=True))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="要不要派单")
    assert_no_critical(snapshot)
    assert_shadow_tools_subset_legacy(snapshot)
    shadow = snapshot.get("shadow_plan") or {}
    diff_types = set((snapshot.get("planning_diff") or {}).get("diff_types") or [])
    assert_true(
        shadow.get("refresh_required") is True or "stale_refresh_mismatch" not in diff_types,
        "stale workorder must have refresh/disclosure, not an unsafe stale mismatch",
    )
    return snapshot


def scenario_ambiguous_context(client: TestClient) -> dict[str, Any]:
    thread_id = "planning-diff.E"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", created_at="2026-06-24T10:00:00"))
    save_thread_artifact(artifact(thread_id=thread_id, asset="J2", created_at="2026-06-24T10:01:00"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    assert_no_critical(snapshot)
    assert_true((snapshot.get("shadow_plan") or {}).get("expected_output") == "clarification", "ambiguous context must clarify")
    return snapshot


def scenario_unauthorized_inheritance(client: TestClient) -> dict[str, Any]:
    thread_id = "planning-diff.F"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="guest")
    snapshot = plan(client, thread_id=thread_id, message="它要不要生成工单？")
    assert_no_critical(snapshot)
    assert_no_sensitive_leak(snapshot, ["J1", "A07089", "/reports/"])
    context = snapshot.get("resolved_context") or {}
    assert_true(not context.get("referenced_artifact_id"), "unauthorized inheritance must not reference artifact")
    return snapshot


def scenario_explicit_device_switch(client: TestClient) -> dict[str, Any]:
    thread_id = "planning-diff.G"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="J2 当前状态怎么样")
    assert_no_critical(snapshot)
    assert_shadow_tools_subset_legacy(snapshot)
    assert_true((snapshot.get("resolved_context") or {}).get("referenced_artifact_id") in {None, ""}, "device switch must not inherit old artifact")
    return snapshot


SCENARIOS: list[tuple[str, Callable[[TestClient], dict[str, Any]]]] = [
    ("A knowledge_lookup", scenario_knowledge_lookup),
    ("B runtime_status", scenario_runtime_status),
    ("C report_handoff", scenario_report_handoff),
    ("D stale_workorder", scenario_stale_workorder),
    ("E ambiguous_context", scenario_ambiguous_context),
    ("F unauthorized_inheritance", scenario_unauthorized_inheritance),
    ("G explicit_device_switch", scenario_explicit_device_switch),
]


def main() -> int:
    client = build_client()
    failed: list[str] = []
    needs_review: list[str] = []
    with client:
        for name, func in SCENARIOS:
            clear_all_artifacts()
            try:
                snapshot = func(client)
                diff = planning_diff(snapshot)
                if diff.get("overall_status") == "needs_review":
                    needs_review.append(name)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                print(f"FAIL {name}: {exc}")
            else:
                print(f"PASS {name}")
    summary = {"passed": len(SCENARIOS) - len(failed), "failed": failed, "needs_review": needs_review}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
