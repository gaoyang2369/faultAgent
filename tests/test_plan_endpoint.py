from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fault_diagnosis import config
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, list_thread_artifacts
from fault_diagnosis.runtime.dev_mode import init_dev_state
from fault_diagnosis.security.contracts import AuthContext
from fault_diagnosis.single_agent.planner import build_plan_snapshot


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
    assert payload["authorization"]["mode"] == "deny"
    assert payload["authorization"]["denied_reason_code"] == "diagnosis_permission_denied"
    assert "save_report" not in payload["planned_tools"]


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
    monkeypatch.setattr("fault_diagnosis.diagnosis.adapters.build_sql_tools_map", fail("sql_tools"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.build_sql_tools_map", fail("stage_sql_tools"))
    monkeypatch.setattr("fault_diagnosis.single_agent.support.tool_access.get_knowledge_tool", fail("rag_tool"))
    monkeypatch.setattr("fault_diagnosis.single_agent.support.tool_access.get_report_tool", fail("report_tool"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.get_knowledge_tool", fail("stage_rag_tool"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.get_report_tool", fail("stage_report_tool"))
    monkeypatch.setattr("fault_diagnosis.single_agent.runner.RestrictedSingleAgentRunner._invoke_json_model", fail("analysis_llm"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.build_templated_final_answer", fail("final_answer"))
    monkeypatch.setattr("fault_diagnosis.diagnosis.artifact_store.save_thread_artifact", fail("artifact_write"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.save_thread_artifact", fail("stage_artifact_write"))

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


def test_planner_has_no_tool_llm_or_artifact_side_effects(monkeypatch) -> None:
    clear_all_artifacts()
    thread_id = "thread.planner.no-side-effect"
    calls: list[str] = []

    def fail(name: str):
        def _inner(*args, **kwargs):  # noqa: ANN001, ARG001
            calls.append(name)
            raise AssertionError(f"{name} must not be called by planner")

        return _inner

    monkeypatch.setattr("fault_diagnosis.diagnosis.adapters.build_sql_tools_map", fail("sql_tools"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.build_sql_tools_map", fail("stage_sql_tools"))
    monkeypatch.setattr("fault_diagnosis.single_agent.support.tool_access.get_knowledge_tool", fail("rag_tool"))
    monkeypatch.setattr("fault_diagnosis.single_agent.support.tool_access.get_report_tool", fail("report_tool"))
    monkeypatch.setattr("fault_diagnosis.single_agent.runner.RestrictedSingleAgentRunner._invoke_json_model", fail("analysis_llm"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.build_templated_final_answer", fail("final_answer"))
    monkeypatch.setattr("fault_diagnosis.diagnosis.artifact_store.save_thread_artifact", fail("artifact_write"))
    monkeypatch.setattr("fault_diagnosis.single_agent.stages.save_thread_artifact", fail("stage_artifact_write"))

    snapshot = build_plan_snapshot(
        message="J1 当前状态如何",
        thread_id=thread_id,
        user_identity="engineer",
        auth_context=AuthContext(user_id="engineer-plan-test", role="engineer", asset_scope=["J1"], table_scope=["*"]),
    )

    assert snapshot.schema_version == "agent_plan_snapshot.v1"
    assert calls == []
    assert list_thread_artifacts(thread_id) == []
