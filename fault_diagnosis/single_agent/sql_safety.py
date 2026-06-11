"""SQL planning prompt and safety helpers for the single-agent runner."""

from __future__ import annotations

import re

from ..diagnosis.contracts import DiagnosisRequest

_SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+`?([a-zA-Z_][\w]*)`?", re.IGNORECASE)

ALLOWED_SQL_TABLES = {"real_data", "device_alarm", "device_metric", "device_fault_data", "fault_records"}
SQL_SCHEMA_CONTEXT = """
仅允许使用以下 MySQL 表，不要使用未列出的表名：
- real_data(timestamp, device_name, device_id, fault_code, spindle_current, spindle_speed, spindle_load, motor_temp, vibration, alarm_status)
- device_alarm(timestamp, alarm_time, device_name, device_id, alarm_code, fault_code, alarm_level, alarm_message, status)
- device_metric(device_id, metric_name, metric_value, record_time)
- device_fault_data(event_time, device_name, device_id, fault_code, spindle_load, vibration, motor_temperature, motor_temp, spindle_current, spindle_speed, alarm_status)
- fault_records(fault_code, description, possible_cause, suggestion, severity)
主轴负载、振动、电机温度优先从 real_data 的 spindle_load、vibration、motor_temp 查询。
只允许生成单条只读 SELECT 查询。
""".strip()


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def extract_sql_table_names(sql_query: str) -> set[str]:
    return {match.group(1).lower() for match in _SQL_TABLE_RE.finditer(sql_query or "")}


def has_unknown_sql_table(sql_query: str) -> bool:
    table_names = extract_sql_table_names(sql_query)
    return any(table_name not in ALLOWED_SQL_TABLES for table_name in table_names)


def is_readonly_sql(sql_query: str) -> bool:
    normalized = (sql_query or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("select") or normalized.startswith("with")


def build_fallback_sql_query(request: DiagnosisRequest) -> str:
    equipment_hint = (request.equipment_hint or "SPINDLE-01").strip()
    fault_code_hint = (request.fault_code_hint or "").strip()
    conditions = []
    if equipment_hint:
        equipment_literal = sql_literal(equipment_hint)
        conditions.append(f"(device_id = {equipment_literal} OR device_name = {equipment_literal})")
    if fault_code_hint:
        fault_code_literal = sql_literal(fault_code_hint)
        conditions.append(f"(fault_code = {fault_code_literal} OR fault_code IS NULL)")
    where_clause = " AND ".join(conditions) or "1=1"
    return (
        "SELECT timestamp, device_name, device_id, fault_code, spindle_current, spindle_speed, "
        "spindle_load, motor_temp, vibration, alarm_status "
        f"FROM real_data WHERE {where_clause} ORDER BY timestamp DESC LIMIT 50"
    )


def build_sql_prompt(request: DiagnosisRequest) -> str:
    return f"""
你是 DCMA 单 Agent 的 SQL 查询规划器。
请输出 JSON：
- sql_query: 单条只读 SELECT SQL
- summary: 一句话说明查询目标

要求：
1. 只输出 JSON。
2. 只允许使用下列可用表结构，不得访问其他表。
3. 优先围绕用户给出的设备、故障码、指标和时间范围查询最近数据。
4. SQL 必须限制返回行数，默认 LIMIT 50。

用户问题：{request.user_message}
分析目标：{request.analysis_goal}
设备提示：{request.equipment_hint}
指标提示：{request.metric_hint}
故障码提示：{request.fault_code_hint}
时间范围提示：{request.time_range_hint}

可用表结构：
{SQL_SCHEMA_CONTEXT}
""".strip()
