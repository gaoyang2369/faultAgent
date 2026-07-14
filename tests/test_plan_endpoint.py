from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fault_diagnosis import config
from fault_diagnosis.server.http.routers.auth import router as auth_router
from fault_diagnosis.server.http.routers.chat import router as chat_router
from fault_diagnosis.server.auth.session_scope import SessionScopeManager
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import clear_all_artifacts, list_thread_artifacts
from fault_diagnosis.server.devtools.dev_mode import init_dev_state


def _app() -> FastAPI:
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("plan-endpoint-test-secret")
    init_dev_state(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return app


def test_plan_endpoint_is_disabled_without_explicit_gate(monkeypatch) -> None:
    monkeypatch.setattr(config, "ENABLE_PLAN_ENDPOINT", False)
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "DEV_AUTH_ENABLED", False)

    with TestClient(_app()) as client:
        response = client.get("/chat/plan", params={"message": "J1 当前状态"})

    assert response.status_code == 404


def test_plan_endpoint_uses_trusted_auth_not_user_identity(monkeypatch) -> None:
    clear_all_artifacts()
    monkeypatch.setattr(config, "ENABLE_PLAN_ENDPOINT", True)
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "DEV_AUTH_ENABLED", True)

    with TestClient(_app()) as client:
        login = client.post("/auth/dev-login", json={"role": "guest"})
        assert login.status_code == 200
        response = client.get(
            "/chat/plan",
            params={
                "message": "排查 J1 A07089 根因",
                "user_identity": "管理员",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["auth_context"]["role"] == "guest"
    compatibility = payload["compatibility_debug"]
    assert compatibility["compatibility_only"] is True
    assert compatibility["authorization"]["mode"] == "deny"
    assert compatibility["authorization"]["denied_reason_code"] == "diagnosis_permission_denied"
    assert "save_report" not in compatibility["planned_tools"]
    assert "relation_to_previous" in compatibility["resolved_context"]
    assert "inherited_slots" in compatibility["resolved_context"]
    assert "primary_goal_id" in compatibility["goal_set"]
    assert isinstance(payload["goals"], list)
    assert compatibility["task_family"] == "diagnosis"
    assert compatibility["workflow_route"]["task_family"] == "diagnosis"
    assert compatibility["policy_id"]
    assert "shadow_plan" not in compatibility
    assert "planning_diff" not in compatibility
    assert "planner_gate" not in compatibility
    assert "readiness" in compatibility
    assert "manual_confirmation" in compatibility
    assert "intent_frame" not in payload
    assert "effective_request_frame" not in payload
    assert payload["canonical_request"]["goals"] == payload["goals"]


def test_plan_endpoint_has_no_tool_llm_or_artifact_side_effects(monkeypatch) -> None:
    clear_all_artifacts()
    thread_id = "thread.plan.no-side-effect"
    calls: list[str] = []

    def fail(name: str):
        def _inner(*args, **kwargs):  # noqa: ANN001, ARG001
            calls.append(name)
            raise AssertionError(f"{name} must not be called by /chat/plan")

        return _inner

    monkeypatch.setattr(config, "ENABLE_PLAN_ENDPOINT", True)
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "DEV_AUTH_ENABLED", True)
    monkeypatch.setattr("fault_diagnosis.domain.diagnosis.adapters.build_sql_tools_map", fail("sql_tools"))
    monkeypatch.setattr("fault_diagnosis.platform.persistence.diagnosis_artifacts.store.save_thread_artifact", fail("artifact_write"))

    with TestClient(_app()) as client:
        login = client.post("/auth/dev-login", json={"role": "engineer", "asset_scope": ["J1"]})
        assert login.status_code == 200
        response = client.get(
            "/chat/plan",
            params={"message": "诊断 J1 A07089 并生成报告", "thread_id": thread_id},
        )

    assert response.status_code == 200
    assert calls == []
    assert list_thread_artifacts(thread_id) == []
