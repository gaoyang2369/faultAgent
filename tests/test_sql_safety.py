from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.single_agent.sql_safety import (
    SQL_SCHEMA_CONTEXT,
    build_fast_sql_plan,
    build_fallback_sql_query,
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
    assert "real_data(id, timestamp, device_name, inverter_name" in SQL_SCHEMA_CONTEXT
    assert "real_data(timestamp, device_name, device_id" not in SQL_SCHEMA_CONTEXT
    assert "real_data 中没有 device_id" in SQL_SCHEMA_CONTEXT


def test_fallback_sql_uses_current_real_data_columns() -> None:
    sql = build_fallback_sql_query(_request(equipment_hint="G120电机1", fault_code_hint="42"))

    assert "FROM real_data" in sql
    assert "device_name = 'G120电机1' OR inverter_name = 'G120电机1'" in sql
    assert "fault_code = '42' OR alarm_code = '42'" in sql
    assert "device_id" not in sql
    assert "spindle_" not in sql
    assert "vibration" not in sql
    assert "alarm_status" not in sql
    assert "ORDER BY real_data.create_time DESC, id DESC" in sql


def test_fallback_sql_queries_latest_rows_without_default_device_filter() -> None:
    sql = build_fallback_sql_query(_request())

    assert "WHERE 1=1" in sql
    assert "SPINDLE-01" not in sql


def test_fallback_sql_treats_dcma_as_system_scope() -> None:
    sql = build_fallback_sql_query(_request(equipment_hint="dcma"))

    assert "WHERE 1=1" in sql
    assert "device_name = 'dcma'" not in sql


def test_fast_sql_plan_handles_status_report_requests() -> None:
    plan = build_fast_sql_plan(_request(user_message="最近dcma运行情况如何？有异常码？可以生成具体报告展示"))

    assert plan is not None
    sql, summary = plan
    assert "FROM real_data" in sql
    assert "ORDER BY real_data.create_time DESC, id DESC LIMIT 50" in sql
    assert "device_name = 'dcma'" not in sql
    assert "real_data 最近 50 条" in summary
