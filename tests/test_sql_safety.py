from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.sql_safety import (
    ALLOWED_SQL_TABLES,
    REAL_DATA_LATEST_TABLE,
    SQL_SCHEMA_CONTEXT,
    build_fast_sql_plan,
    build_fallback_sql_query,
    has_unknown_sql_table,
    select_real_data_table,
)


def _request(**overrides) -> DiagnosisRequest:
    payload = {
        "user_message": "生成 DCMA 当前运行状态报告",
        "user_identity": "游客",
        "equipment_hint": None,
        "metric_hint": None,
        "fault_code_hint": None,
        "time_range_hint": None,
        "needs_report": True,
        "report_format": "markdown",
        "analysis_goal": "生成运行状态报告",
    }
    payload.update(overrides)
    return DiagnosisRequest(**payload)


def test_real_data_schema_context_matches_current_table() -> None:
    assert "real_data_01(id, timestamp, device_name, inverter_name" in SQL_SCHEMA_CONTEXT
    assert "real_data_02(id, timestamp, device_name, inverter_name" in SQL_SCHEMA_CONTEXT
    assert "real_data_03(id, timestamp, device_name, inverter_name" in SQL_SCHEMA_CONTEXT
    assert "real_data(timestamp, device_name, device_id" not in SQL_SCHEMA_CONTEXT
    assert "禁止使用旧表 real_data" in SQL_SCHEMA_CONTEXT
    assert "real_data_01/02/03 中没有 device_id" in SQL_SCHEMA_CONTEXT
    assert "real_data" not in ALLOWED_SQL_TABLES
    assert has_unknown_sql_table("SELECT * FROM real_data LIMIT 1") is True
    assert has_unknown_sql_table("SELECT * FROM real_data_01 LIMIT 1") is False


def test_fallback_sql_uses_current_real_data_columns() -> None:
    sql = build_fallback_sql_query(_request(equipment_hint="G120电机1", fault_code_hint="42"))

    assert f"FROM {REAL_DATA_LATEST_TABLE}" in sql
    assert "device_name IN ('G120电机1')" in sql
    assert "fault_code = '42' OR alarm_code = '42'" in sql
    assert "device_id" not in sql
    assert "spindle_" not in sql
    assert "vibration" not in sql
    assert "alarm_status" not in sql
    assert f"ORDER BY {REAL_DATA_LATEST_TABLE}.create_time DESC, id DESC" in sql


def test_fallback_sql_resolves_asset_alias_to_real_data_source() -> None:
    sql = build_fallback_sql_query(_request(equipment_hint="J1号机"))

    assert f"FROM {REAL_DATA_LATEST_TABLE}" in sql
    assert "device_name IN ('G120电机1')" in sql
    assert "J1号机" not in sql


def test_fallback_sql_queries_latest_rows_without_default_device_filter() -> None:
    sql = build_fallback_sql_query(_request())

    assert "WHERE 1=1" in sql
    assert "SPINDLE-01" not in sql


def test_report_sql_can_inherit_decision_asset_filters() -> None:
    sql = build_fallback_sql_query(_request(), asset_filters=["J1号机"])

    assert "WHERE 1=1" not in sql
    assert "device_name IN ('G120电机1')" in sql
    assert "inverter_name IN" not in sql


def test_fallback_sql_treats_dcma_as_system_scope() -> None:
    sql = build_fallback_sql_query(_request(equipment_hint="dcma"))

    assert "WHERE 1=1" in sql
    assert "device_name = 'dcma'" not in sql


def test_explicit_real_data_shard_can_be_selected() -> None:
    request = _request(user_message="查询 real_data_03 的历史运行状态")
    sql = build_fallback_sql_query(request)

    assert select_real_data_table(request) == "real_data_03"
    assert "FROM real_data_03" in sql
    assert "ORDER BY real_data_03.create_time DESC, id DESC LIMIT 50" in sql


def test_fast_sql_plan_handles_status_report_requests() -> None:
    plan = build_fast_sql_plan(_request(user_message="最近dcma运行情况如何？有异常码？可以生成具体报告展示"))

    assert plan is not None
    sql, summary = plan
    assert f"FROM {REAL_DATA_LATEST_TABLE}" in sql
    assert f"ORDER BY {REAL_DATA_LATEST_TABLE}.create_time DESC, id DESC LIMIT 50" in sql
    assert "device_name = 'dcma'" not in sql
    assert f"{REAL_DATA_LATEST_TABLE} 最近 50 条" in summary


def test_fast_sql_plan_handles_device_fault_diagnosis_requests() -> None:
    plan = build_fast_sql_plan(
        _request(
            user_message="对G120电机1进行故障诊断",
            equipment_hint="G120电机1",
            analysis_goal="对G120电机1进行故障诊断",
        )
    )

    assert plan is not None
    sql, summary = plan
    assert f"FROM {REAL_DATA_LATEST_TABLE}" in sql
    assert "device_name IN ('G120电机1')" in sql
    assert f"ORDER BY {REAL_DATA_LATEST_TABLE}.create_time DESC, id DESC LIMIT 50" in sql
    assert f"{REAL_DATA_LATEST_TABLE} 最近 50 条" in summary


def test_fast_sql_plan_inherits_asset_filters_for_single_device_report() -> None:
    plan = build_fast_sql_plan(
        _request(user_message="生成运行报告"),
        asset_filters=["J1号机"],
    )

    assert plan is not None
    sql, _summary = plan
    assert "WHERE 1=1" not in sql
    assert "device_name IN ('G120电机1')" in sql
