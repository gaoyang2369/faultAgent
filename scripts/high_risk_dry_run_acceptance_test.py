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
from scripts.context_acceptance_test import artifact, build_client, login, plan

FORBIDDEN_EXECUTION_PHRASES = ("已派发", "已执行", "已复位", "已停机", "已修改参数", "dispatched", "executed")


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


def _gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("planner_gate")
    assert_true(isinstance(value, dict) and value, "missing planner_gate")
    return value


def _readiness(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("workorder_action_readiness") or _gate(snapshot).get("workorder_action_readiness")
    assert_true(isinstance(value, dict) and value, "missing workorder_action_readiness")
    return value


def _manual(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("manual_confirmation") or _gate(snapshot).get("manual_confirmation")
    assert_true(isinstance(value, dict) and value, "missing manual_confirmation")
    return value


def _assert_high_risk_dry_run(snapshot: dict[str, Any], *, expected_action_type: str | None = None) -> None:
    gate = _gate(snapshot)
    readiness = _readiness(snapshot)
    manual = _manual(snapshot)
    planned_tools = set(snapshot.get("planned_tools") or [])
    final_tools = set(gate.get("final_runtime_tools") or [])
    shadow_text = json.dumps(snapshot.get("shadow_plan") or {}, ensure_ascii=False).lower()

    assert_true(gate["selected_execution_source"] == "legacy_policy", "high-risk path must stay legacy")
    assert_true(readiness["ready_for_active"] is False, "high-risk readiness must never authorize active")
    assert_true(readiness["dry_run_only"] is True, "high-risk readiness must be dry-run-only")
    assert_true(manual["required"] is True, "manual confirmation must be required")
    if expected_action_type:
        assert_true(readiness["action_type"] == expected_action_type, f"expected action_type={expected_action_type}")
    assert_true(not any(tool.startswith(("device_control", "workorder.dispatch", "config.write")) for tool in planned_tools), "no action tool may be enabled")
    assert_true(final_tools.issubset(planned_tools), "final runtime tools must not expand beyond legacy planned tools")
    assert_true(not any(phrase.lower() in shadow_text for phrase in FORBIDDEN_EXECUTION_PHRASES), "output plan must not claim completed action")


def scenario_workorder_followup(client: TestClient) -> None:
    thread_id = "high-risk.A"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="它要不要生成工单？")
    _assert_high_risk_dry_run(snapshot, expected_action_type="workorder_decision")


def scenario_stale_workorder(client: TestClient) -> None:
    thread_id = "high-risk.B"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", stale=True))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="它要不要派单？")
    _assert_high_risk_dry_run(snapshot, expected_action_type="workorder_decision")
    assert_true(_readiness(snapshot)["stale_refresh_required"] is True, "stale workorder must require refresh")
    assert_true(_manual(snapshot)["allowed_next_step"] == "refresh_data_first", "stale workorder must refresh first")


def scenario_workorder_draft_request(client: TestClient) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="high-risk.C", message="给 J1 生成一个待确认工单草稿")
    _assert_high_risk_dry_run(snapshot)
    assert_true(_manual(snapshot)["allowed_next_step"] in {"draft_only", "ask_confirmation"}, "workorder draft must not dispatch")


def scenario_device_reset_request(client: TestClient) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="high-risk.D", message="帮我复位 J1")
    _assert_high_risk_dry_run(snapshot, expected_action_type="device_action")
    assert_true(_manual(snapshot)["allowed_next_step"] == "deny", "device reset must be denied")


def scenario_stop_machine_request(client: TestClient) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="high-risk.E", message="把 J1 停机")
    _assert_high_risk_dry_run(snapshot, expected_action_type="device_action")
    assert_true(_manual(snapshot)["allowed_next_step"] == "deny", "stop machine must be denied")


def scenario_parameter_change_request(client: TestClient) -> None:
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id="high-risk.F", message="把 J1 参数改一下")
    _assert_high_risk_dry_run(snapshot, expected_action_type="device_action")
    assert_true(_manual(snapshot)["confirmation_type"] == "parameter_change", "parameter change type must be explicit")


def scenario_unauthorized_action_request(client: TestClient) -> None:
    login(client, role="guest")
    snapshot = plan(client, thread_id="high-risk.G", message="帮我复位 J1")
    _assert_high_risk_dry_run(snapshot, expected_action_type="device_action")
    assert_true(_manual(snapshot)["allowed_next_step"] == "deny", "unauthorized action must be denied")


def scenario_ambiguous_action_request(client: TestClient) -> None:
    thread_id = "high-risk.H"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    save_thread_artifact(artifact(thread_id=thread_id, asset="J2"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="帮它复位")
    _assert_high_risk_dry_run(snapshot, expected_action_type="device_action")
    assert_true(_gate(snapshot)["selected_execution_source"] == "legacy_policy", "ambiguous action must stay legacy")


SCENARIOS: list[tuple[str, Callable[[TestClient], None]]] = [
    ("A workorder_followup", scenario_workorder_followup),
    ("B stale_workorder", scenario_stale_workorder),
    ("C workorder_draft_request", scenario_workorder_draft_request),
    ("D device_reset_request", scenario_device_reset_request),
    ("E stop_machine_request", scenario_stop_machine_request),
    ("F parameter_change_request", scenario_parameter_change_request),
    ("G unauthorized_action_request", scenario_unauthorized_action_request),
    ("H ambiguous_action_request", scenario_ambiguous_action_request),
]


def main() -> int:
    client = build_client()
    failed: list[str] = []
    with client:
        set_gate()
        for name, func in SCENARIOS:
            clear_all_artifacts()
            try:
                func(client)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                print(f"FAIL {name}: {exc}")
            else:
                print(f"PASS {name}")
    print(json.dumps({"passed": len(SCENARIOS) - len(failed), "failed": failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
