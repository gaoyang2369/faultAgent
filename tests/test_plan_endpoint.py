from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fault_diagnosis import config
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts
from fault_diagnosis.runtime.dev_mode import init_dev_state


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
                "message": "诊断 J1 A07089 的原因",
                "user_identity": "管理员",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["auth_context"]["role"] == "guest"
    assert payload["authorization"]["mode"] == "degrade"
    assert "save_report" not in payload["planned_tools"]
