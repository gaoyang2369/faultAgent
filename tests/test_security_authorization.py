from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fault_diagnosis.platform.persistence.repositories.user_repository import FileUserRepository, hash_password, verify_password
from fault_diagnosis.domain.security.assets import load_asset_registry
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.domain.security.policy_engine import authorize_workflow
from fault_diagnosis.domain.security.rag_acl import filter_kb_documents
from fault_diagnosis.domain.security.sql_acl import apply_sql_acl
from fault_diagnosis.domain.security.tool_gateway import authorize_tool_call
from fault_diagnosis.server.use_cases.workorder_service import CreateWorkOrderPayload, WorkOrderService
from fault_diagnosis.platform.persistence.repositories.workorder_repository import FileWorkOrderRepository


def _decision(**overrides) -> SimpleNamespace:
    payload = {
        "task_family": "diagnosis",
        "requested_output": "answer",
        "goal_set": {"goals": [{"goal_type": "diagnose_fault"}]},
        "enabled_nodes": {},
        "runtime_tools": [],
        "objects": {},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_password_hash_is_salted_and_verifiable() -> None:
    encoded = hash_password("correct horse battery staple", iterations=100_000, salt=b"0123456789abcdef")

    assert "correct horse" not in encoded
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong", encoded) is False
    assert verify_password("password", "plaintext") is False


def test_guest_has_fixed_server_side_scope() -> None:
    auth = build_auth_context(role="guest", table_scope=["device_alarm"], kb_scopes=["restricted"])

    assert auth.table_scope == ["real_data_01"]
    assert auth.asset_scope == ["g120_motor_1"]
    assert auth.kb_scopes == []
    assert "workflow.fault_diagnosis" not in auth.permissions
    assert "tool.sql.read" in auth.permissions


def test_signed_user_cookie_is_session_bound_and_reloads_server_scope(tmp_path) -> None:
    from fault_diagnosis.server.auth.admin_auth import USER_AUTH_COOKIE_NAME, issue_user_auth_token, resolve_auth_context

    user_path = tmp_path / "users.json"
    user_path.write_text(
        json.dumps(
            [
                {
                    "user_id": "engineer_01",
                    "username": "engineer_01",
                    "password_hash": hash_password("secret", iterations=100_000),
                    "role": "engineer",
                    "asset_scope": ["J1号机"],
                    "table_scope": ["real_data_01"],
                }
            ]
        ),
        encoding="utf-8",
    )
    token = issue_user_auth_token("session-1", "engineer_01")
    request = SimpleNamespace(cookies={USER_AUTH_COOKIE_NAME: token})
    repository = FileUserRepository(path=user_path)

    auth = resolve_auth_context(request, "session-1", user_repository=repository)
    wrong_session = resolve_auth_context(request, "session-2", user_repository=repository)

    assert auth.role == "engineer"
    assert auth.asset_scope == ["J1号机"]
    assert wrong_session.role == "guest"


def test_guest_fault_diagnosis_is_denied() -> None:
    decision = _decision(
        task_family="diagnosis",
        goal_set={"goals": [{"goal_type": "diagnose_fault"}]},
        enabled_nodes={"sql": True, "knowledge": True, "analysis": True, "report": True},
        runtime_tools=["sql_db_query", "query_knowledge_base", "save_report"],
    )

    authorization = authorize_workflow(build_auth_context(role="guest"), decision)

    assert authorization.allowed is False
    assert authorization.mode == "deny"
    assert authorization.denied_reason_code == "diagnosis_permission_denied"
    assert authorization.data_scope["allowed_tables"] == ["real_data_01"]
    assert authorization.data_scope["max_lookback_hours"] == 1
    assert authorization.denied_nodes["fault_diagnosis"] == "missing_workflow_permission"
    assert authorization.denied_nodes["report"] == "missing_report_permission"
    assert authorization.runtime_tools == []
    assert "无法进行故障诊断" in authorization.user_message


def test_guest_report_generation_is_denied_without_degraded_tools() -> None:
    decision = _decision(
        task_family="reporting",
        requested_output="report",
        goal_set={"goals": [{"goal_type": "generate_report"}]},
        enabled_nodes={"sql": True, "knowledge": True, "analysis": True, "report": True},
        runtime_tools=["sql_db_query", "query_knowledge_base", "save_report"],
    )

    authorization = authorize_workflow(build_auth_context(role="guest"), decision)

    assert authorization.allowed is False
    assert authorization.mode == "deny"
    assert authorization.denied_reason_code == "report_permission_denied"
    assert authorization.denied_nodes["report"] == "missing_report_permission"
    assert authorization.runtime_tools == []
    assert "无法生成 DCMA 运行报告" in authorization.user_message


def test_engineer_cannot_authorize_unassigned_asset() -> None:
    auth = build_auth_context(
        user_id="engineer_01",
        role="engineer",
        asset_scope=["J1号机"],
        table_scope=["real_data_01"],
    )
    decision = _decision(
        task_family="diagnosis",
        goal_set={"goals": [{"goal_type": "diagnose_fault"}]},
        objects={"device_ids": ["J2号机"]},
    )

    authorization = authorize_workflow(auth, decision)

    assert authorization.allowed is False
    assert authorization.denied_reason_code == "asset_out_of_scope"


def test_guest_sql_acl_forces_table_time_and_limit() -> None:
    result = apply_sql_acl(
        "SELECT device_name, current_actual FROM real_data_01 ORDER BY create_time DESC LIMIT 500",
        auth=build_auth_context(role="guest"),
    )

    assert result.allowed is True
    assert "FROM real_data_01" in result.sql_query
    assert "G120电机1" in result.sql_query
    assert "device_name IN ('G120电机1')" in result.sql_query
    assert "create_time >= NOW() - INTERVAL 1 HOUR" in result.sql_query
    assert "SELECT MAX(create_time) FROM real_data_01" in result.sql_query
    assert "guest_asset_scope" in result.filters_applied
    assert result.sql_query.endswith("LIMIT 50")
    assert apply_sql_acl(
        "SELECT * FROM device_alarm LIMIT 10",
        auth=build_auth_context(role="guest"),
    ).blocked_reason_code == "guest_table_out_of_scope"
    assert apply_sql_acl(
        "SELECT * FROM real_data_01 /* create_time >= NOW() - INTERVAL 1 HOUR */ LIMIT 10",
        auth=build_auth_context(role="guest"),
    ).blocked_reason_code == "unsupported_sql_shape"


def test_admin_sql_acl_uses_latest_row_anchor_when_configured(monkeypatch) -> None:
    monkeypatch.delenv("DCMA_SQL_TIME_ANCHOR", raising=False)
    result = apply_sql_acl(
        "SELECT * FROM real_data_01 ORDER BY create_time DESC LIMIT 500",
        auth=build_auth_context(role="admin"),
    )

    assert result.allowed is True
    assert "create_time >= NOW() - INTERVAL 7 DAY" in result.sql_query
    assert "NOT EXISTS (SELECT 1 FROM real_data_01" in result.sql_query
    assert "SELECT MAX(create_time) FROM real_data_01" in result.sql_query
    assert result.sql_query.endswith("LIMIT 50")


def test_admin_sql_acl_respects_explicit_now_time_anchor(monkeypatch) -> None:
    monkeypatch.setenv("DCMA_SQL_TIME_ANCHOR", "now")
    result = apply_sql_acl(
        "SELECT * FROM real_data_01 ORDER BY create_time DESC LIMIT 500",
        auth=build_auth_context(role="admin"),
    )

    assert result.allowed is True
    assert "create_time >= NOW() - INTERVAL 7 DAY" in result.sql_query
    assert "NOT EXISTS" not in result.sql_query
    assert "SELECT MAX(create_time)" not in result.sql_query


def test_guest_sql_acl_denies_unassigned_asset() -> None:
    result = apply_sql_acl(
        "SELECT * FROM real_data_01 WHERE device_name = 'G120电机2' ORDER BY create_time DESC LIMIT 10",
        auth=build_auth_context(role="guest"),
        request=SimpleNamespace(equipment_hint="G120电机2"),
        decision=_decision(objects={"device_ids": ["G120电机2"]}),
    )

    assert result.allowed is False
    assert result.blocked_reason_code == "asset_out_of_scope"


def test_engineer_sql_acl_injects_asset_scope() -> None:
    auth = build_auth_context(
        user_id="engineer_01",
        role="engineer",
        asset_scope=["J1号机", "pump_001"],
        table_scope=["real_data_01"],
    )
    request = SimpleNamespace(equipment_hint="J1号机")
    result = apply_sql_acl(
        "SELECT * FROM real_data_01 ORDER BY create_time DESC",
        auth=auth,
        request=request,
        decision=_decision(objects={"device_ids": ["J1号机"]}),
    )

    assert result.allowed is True
    assert "FROM real_data_01" in result.sql_query
    assert "pump_001" not in result.sql_query
    assert "engineer_asset_table_scope" in result.filters_applied


def test_engineer_asset_scope_allows_registered_aliases() -> None:
    auth = build_auth_context(
        user_id="engineer_01",
        role="engineer",
        asset_scope=["J1号机"],
        table_scope=["real_data_01"],
    )
    result = apply_sql_acl(
        "SELECT * FROM real_data_01 ORDER BY create_time DESC",
        auth=auth,
        request=SimpleNamespace(equipment_hint="G120电机1"),
        decision=_decision(objects={"device_ids": ["G120电机1"]}),
    )

    assert result.allowed is True
    assert "FROM real_data_01" in result.sql_query
    assert "device_name IN ('G120电机1')" not in result.sql_query
    assert "engineer_asset_table_scope" in result.filters_applied


def test_sql_acl_uses_configured_row_level_asset_terms(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "asset_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset_id": "g120_motor_1",
                        "display_name": "G120电机1",
                        "aliases": ["G120电机1", "J1号机"],
                        "data_sources": [{"table": "real_data_01", "device_name": "DB-G120-1"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ASSET_REGISTRY_PATH", str(registry_path))
    load_asset_registry.cache_clear()
    auth = build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"])

    try:
        result = apply_sql_acl(
            "SELECT * FROM real_data_01 ORDER BY create_time DESC",
            auth=auth,
            request=SimpleNamespace(equipment_hint="G120电机1"),
            decision=_decision(objects={"device_ids": ["G120电机1"]}),
        )
    finally:
        load_asset_registry.cache_clear()

    assert result.allowed is True
    assert "device_name IN ('DB-G120-1')" in result.sql_query
    assert "engineer_asset_scope" in result.filters_applied


def test_rag_acl_filters_uploaded_documents_by_role() -> None:
    docs = [
        {"preview": "公开手册", "source_type": "knowledge_base", "visibility": "public"},
        {"preview": "内部复盘", "source_type": "uploaded_file", "visibility": "internal"},
        {"preview": "敏感方案", "source_type": "uploaded_file", "visibility": "restricted"},
    ]

    assert [item["preview"] for item in filter_kb_documents(docs, auth=build_auth_context(role="guest"))] == [
        "公开手册"
    ]
    assert [item["preview"] for item in filter_kb_documents(docs, auth=build_auth_context(role="engineer"))] == [
        "公开手册",
        "内部复盘",
    ]


def test_tool_gateway_denies_guest_report_tool() -> None:
    authorization = authorize_tool_call(build_auth_context(role="guest"), "save_report")

    assert authorization.allowed is False
    assert authorization.denied_reason_code == "missing_tool_permission"


def test_workorder_service_enforces_http_equivalent_permissions(tmp_path) -> None:
    service = WorkOrderService(repository=FileWorkOrderRepository(root_dir=tmp_path))
    payload = CreateWorkOrderPayload(
        title="J1号机排查",
        equipment_object="J1号机",
        thread_id="thread-1",
        trace_id="trace-1",
        status="已派单",
    )
    with pytest.raises(PermissionError):
        service.create_work_order(payload, auth_context=build_auth_context(role="guest"))

    engineer = build_auth_context(
        user_id="engineer_01",
        role="engineer",
        asset_scope=["J1号机"],
        table_scope=["real_data_01"],
    )
    record = service.create_work_order(payload, auth_context=engineer)["work_order"]

    assert record["status"] == "待派单"
    assert record["created_by"] == "engineer_01"
    assert record["authorized_asset_scope"] == ["J1号机"]
