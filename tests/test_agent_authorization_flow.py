from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fault_diagnosis import config
from fault_diagnosis.api import admin_pdfs as admin_pdfs_api
from fault_diagnosis.api.admin_pdfs import router as admin_pdfs_router
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.auth.admin_auth import DEV_AUTH_COOKIE_NAME
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.runtime.dev_mode import build_dev_authorization, stream_dev_chat_events
from fault_diagnosis.security.permissions import build_auth_context, build_dev_auth_context
from fault_diagnosis.security.policy_engine import authorize_workflow
from fault_diagnosis.security.sql_acl import apply_sql_acl
from fault_diagnosis.security.tool_gateway import authorize_tool_call
from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.workflow.nodes import build_permission_check_result


class _FakeAdminPdfService:
    def list_records(self):
        return {"records": []}

    def save_upload(self, *, filename, content_type, content):
        assert filename == "manual.pdf"
        assert content_type == "application/pdf"
        assert content.startswith(b"%PDF")
        return SimpleNamespace(
            payload={"uploaded": True, "file_name": filename},
            status_code=201,
            process_record_id=None,
        )


@pytest.fixture()
def auth_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(config, "DEV_AUTH_ENABLED", True)
    monkeypatch.setattr(admin_pdfs_api, "_admin_pdf_service", lambda: _FakeAdminPdfService())
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("authorization-flow-test-secret")
    app.include_router(auth_router)
    app.include_router(admin_pdfs_router)
    return TestClient(app)


def _dev_login(client: TestClient, role: str) -> dict:
    response = client.post("/auth/dev-login", json={"role": role})
    assert response.status_code == 200
    return response.json()


def _decision(task_type: str, *, device: str | None = None, report: bool = False) -> SingleAgentDecision:
    return SingleAgentDecision(
        primary_task_type=task_type,
        objects={"device_ids": [device] if device else []},
        enabled_nodes={
            "sql": task_type != "knowledge_qa",
            "knowledge": True,
            "analysis": True,
            "report": report,
        },
        runtime_tools=[
            "sql_db_query_checker",
            "sql_db_query",
            "query_knowledge_base",
            *(["save_report"] if report else []),
        ],
    )


def _parse_sse(frames: list[str]) -> list[dict]:
    events: list[dict] = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))
    return events


async def _collect_dev_events(message: str, role: str) -> list[dict]:
    app = SimpleNamespace(
        state=SimpleNamespace(dev_messages={}, dev_todos={}, dev_updated_at={})
    )
    frames = [
        frame
        async for frame in stream_dev_chat_events(
            app,
            message,
            "thread-auth-test",
            "管理员",  # deliberate spoof: AuthContext remains authoritative
            auth_context=build_dev_auth_context(role),
        )
    ]
    return _parse_sse(frames)


def test_dev_login_identity_is_signed_and_frontend_identity_cannot_escalate(auth_client: TestClient) -> None:
    login_identity = _dev_login(auth_client, "guest")
    assert login_identity["role"] == "guest"
    assert login_identity["auth_method"] == "dev-login"
    assert login_identity["allowed_tables"] == ["real_data_01"]
    assert login_identity["asset_scope"] == ["g120_motor_1"]
    assert DEV_AUTH_COOKIE_NAME in auth_client.cookies

    identity = auth_client.get("/auth/identity", params={"user_identity": "管理员"}).json()
    assert identity["role"] == "guest"
    assert identity["is_admin"] is False
    assert "admin.pdf.manage" not in identity["permissions"]

    signed_cookie = auth_client.cookies.get(DEV_AUTH_COOKIE_NAME)
    auth_client.cookies.set(DEV_AUTH_COOKIE_NAME, f"{signed_cookie}tampered")
    tampered_identity = auth_client.get("/auth/identity").json()
    assert tampered_identity["role"] == "guest"
    assert tampered_identity["auth_method"] is None


def test_dev_login_signs_requested_development_identity_scope(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/auth/dev-login",
        json={
            "role": "engineer",
            "user_id": "engineer_01",
            "asset_scope": ["J1号机"],
            "allowed_tables": ["real_data_01", "device_alarm", "fault_records"],
        },
    )

    assert response.status_code == 200
    login_identity = response.json()
    assert login_identity["role"] == "engineer"
    assert login_identity["user_id"] == "engineer_01"
    assert login_identity["asset_scope"] == ["J1号机"]
    assert login_identity["allowed_tables"] == ["real_data_01", "device_alarm", "fault_records"]

    identity = auth_client.get("/auth/identity").json()
    assert identity["role"] == "engineer"
    assert identity["user_id"] == "engineer_01"
    assert identity["asset_scope"] == ["J1号机"]
    assert identity["allowed_tables"] == ["real_data_01", "device_alarm", "fault_records"]


def test_dev_login_is_unavailable_when_development_auth_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "DEV_AUTH_ENABLED", False)
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("production-test-secret")
    app.include_router(auth_router)

    with TestClient(app) as client:
        response = client.post("/auth/dev-login", json={"role": "admin"})
        malformed_response = client.post("/auth/dev-login")

    assert response.status_code in {403, 404}
    assert malformed_response.status_code in {403, 404}


def test_guest_can_query_public_fault_code() -> None:
    events = asyncio.run(_collect_dev_events("故障码 F01002 是什么意思", "guest"))
    complete = next(event for event in events if event.get("type") == "chat_complete")
    tools = [event.get("tool") for event in events if event.get("type") == "tool_start"]

    assert complete["authorization"]["allowed"] is True
    assert complete["decision"]["primary_task_type"] == "knowledge_qa"
    assert "query_knowledge_base" in tools
    assert complete["authorization"]["kb_scope"]["allowed_visibility"] == ["public"]


def test_guest_can_only_query_real_data_01_for_the_last_hour() -> None:
    guest = build_dev_auth_context("guest")
    allowed = apply_sql_acl(
        "SELECT device_name, current_actual FROM real_data_01 ORDER BY create_time DESC LIMIT 500",
        auth=guest,
    )
    denied = apply_sql_acl("SELECT * FROM device_alarm LIMIT 10", auth=guest)

    assert allowed.allowed is True
    assert "device_name IN ('G120电机1')" in allowed.sql_query
    assert "create_time >= NOW() - INTERVAL 1 HOUR" in allowed.sql_query
    assert allowed.sql_query.endswith("LIMIT 50")
    assert denied.allowed is False
    assert denied.blocked_reason_code == "guest_table_out_of_scope"


def test_guest_cannot_diagnose_generate_report_or_upload_pdf(auth_client: TestClient) -> None:
    guest = build_dev_auth_context("guest")
    authorization = authorize_workflow(
        guest,
        _decision("fault_diagnosis", device="J1号机", report=True),
    )

    assert authorization.mode == "degrade"
    assert authorization.denied_nodes["fault_diagnosis"] == "missing_workflow_permission"
    assert authorization.denied_nodes["report"] == "missing_report_permission"
    assert "save_report" not in authorization.runtime_tools
    assert authorize_tool_call(guest, "save_report").allowed is False

    _dev_login(auth_client, "guest")
    response = auth_client.post(
        "/admin/pdfs",
        files={"file": ("manual.pdf", b"%PDF-1.4 guest", "application/pdf")},
    )
    assert response.status_code == 403


def test_engineer_can_diagnose_assigned_asset_but_not_other_assets() -> None:
    engineer = build_dev_auth_context("engineer")
    assigned = authorize_workflow(engineer, _decision("fault_diagnosis", device="J1号机"))
    unassigned = authorize_workflow(engineer, _decision("fault_diagnosis", device="J2号机"))

    assert assigned.allowed is True
    assert assigned.mode == "allow"
    assert unassigned.allowed is False
    assert unassigned.denied_reason_code == "asset_out_of_scope"


def test_admin_can_query_all_assets_and_manage_pdfs(auth_client: TestClient) -> None:
    admin = build_dev_auth_context("admin")
    authorization = authorize_workflow(admin, _decision("fault_diagnosis", device="J99号机"))
    assert authorization.allowed is True
    assert "real_data_03" in authorization.data_scope["allowed_tables"]

    identity = _dev_login(auth_client, "admin")
    assert identity["role"] == "admin"
    assert "admin.pdf.manage" in identity["permissions"]
    assert auth_client.get("/admin/pdfs").status_code == 200
    upload = auth_client.post(
        "/admin/pdfs",
        files={"file": ("manual.pdf", b"%PDF-1.4 admin", "application/pdf")},
    )
    assert upload.status_code == 201


@pytest.mark.parametrize("role", ["guest", "engineer", "admin"])
def test_no_role_can_directly_execute_device_control(role: str) -> None:
    auth = build_dev_auth_context(role)
    decision = _decision("action_request", device="J1号机")
    permission_check = build_permission_check_result(decision, user_identity=auth.display_name)

    assert authorize_tool_call(auth, "device_control.write").allowed is False
    assert permission_check["allowed"] is False
    assert permission_check["decision"] == "draft_or_confirmation_only"


def test_local_sse_authorization_tool_and_report_contract() -> None:
    guest_events = asyncio.run(_collect_dev_events("诊断 J1号机异常并生成报告", "guest"))
    guest_complete = next(event for event in guest_events if event.get("type") == "chat_complete")
    guest_tools = [event.get("tool") for event in guest_events if event.get("type") == "tool_start"]
    assert guest_complete["authorization"]["mode"] == "deny"
    assert guest_complete["authorization"]["denied_reason_code"] == "report_permission_denied"
    assert guest_complete["ui_payload"]["type"] == "report_blocked"
    assert guest_complete["report_url"] is None
    assert guest_tools == []

    admin_events = asyncio.run(_collect_dev_events("生成 J99号机诊断报告", "admin"))
    admin_complete = next(event for event in admin_events if event.get("type") == "chat_complete")
    admin_tools = [event.get("tool") for event in admin_events if event.get("type") == "tool_start"]
    assert admin_complete["authorization"]["mode"] == "allow"
    assert "save_report" in admin_tools
    assert admin_complete["report_url"].startswith("/reports/local_dev_report_")
