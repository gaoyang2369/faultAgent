from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_diagnosis import config
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.runtime.dev_mode import init_dev_state


def build_client() -> TestClient:
    config.ENABLE_PLAN_ENDPOINT = True
    config.LOCAL_DEV_MODE = True
    config.DEV_AUTH_ENABLED = True
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("context-acceptance-secret")
    init_dev_state(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return TestClient(app)


def artifact(
    *,
    thread_id: str,
    asset: str = "J1",
    fault_code: str = "A07089",
    created_at: str = "2026-06-24T10:00:00",
    stale: bool = False,
    snapshot: dict[str, Any] | None = None,
) -> DiagnosisArtifactEnvelope:
    freshness = "已滞后" if stale else "实时性良好"
    payload: dict[str, Any] = {
        "request": {
            "equipment_hint": asset,
            "fault_code_hint": fault_code,
            "analysis_goal": f"生成 {asset} 运行报告",
        },
        "decision": {
            "active_case_id": f"eb_{asset}_{fault_code}",
            "objects": {"device_ids": [asset], "alarm_codes": [fault_code]},
            "context_resolution": {
                "active_asset": asset,
                "active_fault_codes": [fault_code],
                "last_evidence_bundle_id": f"eb_{asset}_{fault_code}",
                "last_report_url": f"{asset}-{fault_code}.html",
            },
            "time_window": {"default_strategy": "last_2h"},
        },
        "analysis_artifact": {
            "success": True,
            "conclusion": f"{asset} {fault_code} 持续出现。",
            "basis": [f"{fault_code} 持续出现"],
            "recommendations": ["刷新当前状态后确认是否派发"],
        },
        "workorder_decision": {
            "need_workorder": True,
            "status": "待确认",
            "reason": "上一轮诊断建议生成待确认工单草稿。",
        },
        "report_artifact": {
            "success": True,
            "report_filename": f"{asset}-{fault_code}.html",
            "report_url": f"/reports/{asset}-{fault_code}.html",
            "save_result": f"/reports/{asset}-{fault_code}.html",
        },
        "evidence_bundle": {"bundle_id": f"eb_{asset}_{fault_code}", "trace_id": f"trace_{asset}"},
        "operation_report_payload": {
            "asset": asset,
            "status_level": "告警 / 需确认",
            "current_event": fault_code,
            "data_freshness_label": freshness,
            "data_currentness_label": "STALE / 不代表实时状态" if stale else "CURRENT",
            "evidence_summary": [f"{fault_code} 持续出现"],
            "next_action": "刷新当前状态后确认是否派发",
        },
    }
    if snapshot is not None:
        payload["case_state_snapshot"] = snapshot
    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
        thread_id=thread_id,
        created_at=created_at,
        request_summary=f"生成 {asset} 运行报告",
        final_answer=f"{asset} {fault_code} 运行报告已生成。" + (" 数据已滞后。" if stale else ""),
        report_filename=f"{asset}-{fault_code}.html",
        payload=payload,
        evidence=[],
    )


def login(client: TestClient, *, role: str = "engineer", asset_scope: list[str] | None = None) -> None:
    payload: dict[str, Any] = {"role": role}
    if asset_scope is not None:
        payload["asset_scope"] = asset_scope
    response = client.post("/auth/dev-login", json=payload)
    assert response.status_code == 200, response.text


def plan(client: TestClient, *, thread_id: str, message: str) -> dict[str, Any]:
    response = client.get(
        "/chat/plan",
        params={"thread_id": thread_id, "message": message, "user_identity": "工程师"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def scenario_report_then_workorder(client: TestClient) -> None:
    thread_id = "acceptance.A"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", stale=True))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="从结果来看貌似有故障呀？是不是要生成工单？")
    context = snapshot["resolved_context"]
    assert_true(context["relation_to_previous"] == "action_followup", "A relation must be action_followup")
    assert_true(bool(context["referenced_artifact_id"]), "A must reference previous artifact")
    assert_true(context["inherited_slots"].get("device") == "J1", "A must inherit J1")
    assert_true(context["stale_evidence"] is True, "A must mark stale evidence")
    assert_true(snapshot["evidence_gaps"]["should_refresh_runtime_data"] is True, "A stale workorder must refresh runtime data")
    assert_true(snapshot["task_family"] in {"diagnosis", "action_or_workorder"}, "A must expose stable task_family")
    assert_true(snapshot["shadow_plan"]["planner_mode"] == "shadow", "A must expose shadow planner summary")
    assert_true(snapshot["shadow_plan"]["refresh_required"] is True, "A shadow plan must require refresh")
    assert_true("decide_workorder" in snapshot["goal_set"]["goal_types"], "A must include workorder goal")
    assert_true("refresh_current_status" in snapshot["goal_set"]["goal_types"], "A stale workorder must include refresh goal")
    assert_true("J2" not in json.dumps(context, ensure_ascii=False), "A must not bind unrelated J2")


def scenario_status_then_report(client: TestClient) -> None:
    thread_id = "acceptance.B"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="那导出报告吧")
    context = snapshot["resolved_context"]
    assert_true(context["relation_to_previous"] == "report_handoff", "B relation must be report_handoff")
    assert_true(bool(context["referenced_artifact_id"]), "B must reference previous artifact")
    assert_true(snapshot["evidence_gaps"]["evidence_mode"] in {"reuse_previous_artifact", "reuse_and_refresh_status"}, "B must reuse evidence")
    assert_true(snapshot["task_family"] == "reporting", "B must expose reporting task_family")
    assert_true(snapshot["shadow_plan"]["expected_output"] == "report", "B shadow plan must expect report")
    assert_true(snapshot["enabled_nodes"].get("report") is True, "B must enable report node")
    assert_true("generate_report" in snapshot["goal_set"]["goal_types"], "B must include report goal")


def scenario_explicit_device_switch(client: TestClient) -> None:
    thread_id = "acceptance.C"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="J2 当前状态怎么样")
    context = snapshot["resolved_context"]
    assert_true(context["relation_to_previous"] in {"new_case", "correction"}, "C relation must be new_case/correction")
    assert_true(not context.get("referenced_artifact_id"), "C must not inherit J1 artifact")
    assert_true(context["inherited_slots"].get("device") != "J1", "C must not inherit J1")
    assert_true(snapshot["task_family"] == "runtime_status", "C must expose runtime_status task_family")
    assert_true("sql" in snapshot["shadow_plan"]["enabled_node_names"], "C shadow plan must include SQL")
    assert_true("check_runtime_status" in snapshot["goal_set"]["goal_types"], "C must include current status goal")


def scenario_ambiguous_reference(client: TestClient) -> None:
    thread_id = "acceptance.D"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1", created_at="2026-06-24T10:00:00"))
    save_thread_artifact(artifact(thread_id=thread_id, asset="J2", created_at="2026-06-24T10:01:00"))
    login(client, role="engineer", asset_scope=["J1", "J2"])
    snapshot = plan(client, thread_id=thread_id, message="它严重吗？")
    context = snapshot["resolved_context"]
    assert_true(context["relation_to_previous"] == "ambiguous", "D relation must be ambiguous")
    assert_true(bool(context["missing_context"]), "D must ask user to clarify object")
    assert_true(not context.get("referenced_artifact_id"), "D must not pick latest artifact")
    assert_true("clarify_missing_context" in snapshot["goal_set"]["goal_types"], "D must include clarification goal")
    assert_true(bool(snapshot["goal_set"]["blocked_goals"]), "D must have blocked business goal")


def scenario_unauthorized_inheritance(client: TestClient) -> None:
    thread_id = "acceptance.E"
    save_thread_artifact(artifact(thread_id=thread_id, asset="J1"))
    login(client, role="guest")
    snapshot = plan(client, thread_id=thread_id, message="它要不要生成工单？")
    context = snapshot["resolved_context"]
    text = json.dumps(context, ensure_ascii=False)
    assert_true(not context.get("referenced_artifact_id"), "E must not reference unauthorized artifact")
    assert_true(context.get("inherited_slots") == {}, "E must not inherit slots")
    assert_true(context.get("pending_action_count") == 0, "E must not inherit pending actions")
    assert_true(bool(context.get("missing_context")), "E must explain missing context")
    assert_true("授权范围" in context.get("context_resolution_reason", ""), "E reason must mention authorization")
    assert_true(snapshot["goal_set"]["primary_goal_id"], "E must keep non-empty goal set")
    assert_true("J1" not in text and "/reports/" not in text and "A07089" not in text, "E must not leak previous details")


def scenario_bad_snapshot_fallback(client: TestClient) -> None:
    thread_id = "acceptance.F"
    save_thread_artifact(
        artifact(
            thread_id=thread_id,
            asset="J1",
            snapshot={
                "schema_version": "case_state_snapshot.v0",
                "thread_id": thread_id,
                "case_id": "bad",
                "active_asset": "BAD",
            },
        )
    )
    login(client, role="engineer", asset_scope=["J1"])
    snapshot = plan(client, thread_id=thread_id, message="基于刚才结果导出报告")
    context = snapshot["resolved_context"]
    assert_true(context["relation_to_previous"] == "report_handoff", "F relation must be report_handoff")
    assert_true(context["inherited_slots"].get("device") == "J1", "F must fall back to artifact payload")
    assert_true("schema_version" in context["context_resolution_reason"], "F must expose snapshot schema fallback reason")
    assert_true("generate_report" in snapshot["goal_set"]["goal_types"], "F must include report goal")


SCENARIOS: list[tuple[str, Callable[[TestClient], None]]] = [
    ("A report_then_workorder", scenario_report_then_workorder),
    ("B status_then_report", scenario_status_then_report),
    ("C explicit_device_switch", scenario_explicit_device_switch),
    ("D ambiguous_reference", scenario_ambiguous_reference),
    ("E unauthorized_inheritance", scenario_unauthorized_inheritance),
    ("F bad_snapshot_fallback", scenario_bad_snapshot_fallback),
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
