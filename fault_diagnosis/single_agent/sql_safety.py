"""SQL planning prompt and safety helpers for the single-agent runner."""

from __future__ import annotations

import re

from ..diagnosis.contracts import DiagnosisRequest

_SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+`?([a-zA-Z_][\w]*)`?", re.IGNORECASE)

REAL_DATA_TABLES = ("real_data_01", "real_data_02", "real_data_03")
REAL_DATA_LATEST_TABLE = REAL_DATA_TABLES[0]
ALLOWED_SQL_TABLES = {
    *REAL_DATA_TABLES,
    "device_alarm",
    "device_metric",
    "device_fault_data",
    "fault_records",
}
GENERIC_EQUIPMENT_HINTS = {"dcma", "dcma系统", "dcma 系统", "系统", "全系统", "当前系统"}
FAST_REAL_DATA_KEYWORDS = (
    "运行",
    "运行情况",
    "运行状态",
    "状态",
    "当前",
    "最近",
    "异常",
    "异常码",
    "报警",
    "告警",
    "故障码",
    "报告",
    "巡检",
)
SQL_SCHEMA_CONTEXT = """
仅允许使用以下 MySQL 表，不要使用未列出的表名：
- real_data_01(id, timestamp, device_name, inverter_name, date, time, status, fault_code, alarm_code, control_word, status_word, dc_voltage, speed_setpoint, speed_actual, current_actual, torque_setpoint, torque_actual, air_intake_temp, motor_temp, inverter_temp, actual_power, field_current, torque_current, system_run_time, inverter_radiator_temp, inverter_load_rate, motor_load_rate, pulse_frequency, motor_power, feedback_power, create_time)
- real_data_02(id, timestamp, device_name, inverter_name, date, time, status, fault_code, alarm_code, control_word, status_word, dc_voltage, speed_setpoint, speed_actual, current_actual, torque_setpoint, torque_actual, air_intake_temp, motor_temp, inverter_temp, actual_power, field_current, torque_current, system_run_time, inverter_radiator_temp, inverter_load_rate, motor_load_rate, pulse_frequency, motor_power, feedback_power, create_time)
- real_data_03(id, timestamp, device_name, inverter_name, date, time, status, fault_code, alarm_code, control_word, status_word, dc_voltage, speed_setpoint, speed_actual, current_actual, torque_setpoint, torque_actual, air_intake_temp, motor_temp, inverter_temp, actual_power, field_current, torque_current, system_run_time, inverter_radiator_temp, inverter_load_rate, motor_load_rate, pulse_frequency, motor_power, feedback_power, create_time)
- device_alarm(timestamp, alarm_time, device_name, device_id, alarm_code, fault_code, alarm_level, alarm_message, status)
- device_metric(device_id, metric_name, metric_value, record_time)
- device_fault_data(event_time, device_name, device_id, fault_code, spindle_load, vibration, motor_temperature, motor_temp, spindle_current, spindle_speed, alarm_status)
- fault_records(fault_code, description, possible_cause, suggestion, severity)
禁止使用旧表 real_data；DCMA 运行数据只使用 real_data_01、real_data_02、real_data_03。
最近/当前/最新运行情况默认查询 real_data_01；只有用户明确要求历史分表时才查询 real_data_02 或 real_data_03。
real_data_01/02/03 中没有 device_id、spindle_current、spindle_speed、spindle_load、vibration、alarm_status 字段；设备过滤必须使用 device_name 或 inverter_name。
状态/报警优先查询 real_data_01/02/03 的 status、fault_code、alarm_code、control_word、status_word。
运行指标优先查询 dc_voltage、speed_setpoint、speed_actual、current_actual、torque_setpoint、torque_actual、air_intake_temp、motor_temp、inverter_temp、actual_power、field_current、torque_current、inverter_radiator_temp、inverter_load_rate、motor_load_rate、pulse_frequency、motor_power、feedback_power。
最近数据优先按 create_time DESC, id DESC 排序；date/time 是字符串字段，只有明确需要展示原始采集时间时再选择。
只允许生成单条只读 SELECT 查询。
""".strip()

REAL_DATA_FALLBACK_COLUMN_NAMES = (
    "id",
    "timestamp",
    "device_name",
    "inverter_name",
    "date",
    "time",
    "status",
    "fault_code",
    "alarm_code",
    "control_word",
    "status_word",
    "dc_voltage",
    "speed_setpoint",
    "speed_actual",
    "current_actual",
    "torque_setpoint",
    "torque_actual",
    "air_intake_temp",
    "motor_temp",
    "inverter_temp",
    "actual_power",
    "field_current",
    "torque_current",
    "system_run_time",
    "inverter_radiator_temp",
    "inverter_load_rate",
    "motor_load_rate",
    "pulse_frequency",
    "motor_power",
    "feedback_power",
    "create_time",
)
REAL_DATA_FALLBACK_SELECT_EXPRESSIONS = tuple(
    "DATE_FORMAT(create_time, '%Y-%m-%d %H:%i:%s') AS create_time"
    if column == "create_time"
    else column
    for column in REAL_DATA_FALLBACK_COLUMN_NAMES
)
REAL_DATA_FALLBACK_COLUMNS = ", ".join(REAL_DATA_FALLBACK_SELECT_EXPRESSIONS)


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def is_generic_equipment_hint(value: str | None) -> bool:
    compact = (value or "").strip().replace(" ", "").lower()
    return bool(compact and compact in {item.replace(" ", "").lower() for item in GENERIC_EQUIPMENT_HINTS})


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


def select_real_data_table(request: DiagnosisRequest) -> str:
    """Choose the DCMA running-data shard, defaulting recent/current requests to real_data_01."""

    text = " ".join(
        item
        for item in (
            request.user_message,
            request.analysis_goal,
            request.metric_hint or "",
            request.time_range_hint or "",
        )
        if item
    ).lower()
    return next((table_name for table_name in REAL_DATA_TABLES if table_name in text), REAL_DATA_LATEST_TABLE)


def build_fallback_sql_query(request: DiagnosisRequest, *, table_name: str | None = None) -> str:
    table_name = table_name if table_name in REAL_DATA_TABLES else select_real_data_table(request)
    equipment_hint = (request.equipment_hint or "").strip()
    fault_code_hint = (request.fault_code_hint or "").strip()
    conditions = []
    if equipment_hint and not is_generic_equipment_hint(equipment_hint):
        equipment_literal = sql_literal(equipment_hint)
        conditions.append(f"(device_name = {equipment_literal} OR inverter_name = {equipment_literal})")
    if fault_code_hint:
        fault_code_literal = sql_literal(fault_code_hint)
        conditions.append(f"(fault_code = {fault_code_literal} OR alarm_code = {fault_code_literal})")
    where_clause = " AND ".join(conditions) or "1=1"
    return (
        f"SELECT {REAL_DATA_FALLBACK_COLUMNS} "
        f"FROM {table_name} WHERE {where_clause} "
        f"ORDER BY {table_name}.create_time DESC, id DESC LIMIT 50"
    )


def build_fast_sql_plan(request: DiagnosisRequest) -> tuple[str, str] | None:
    """Return a deterministic SQL plan for common DCMA running-data status/report queries."""

    text = " ".join(
        item
        for item in (
            request.user_message,
            request.analysis_goal,
            request.metric_hint or "",
            request.time_range_hint or "",
        )
        if item
    )
    if not any(keyword in text for keyword in FAST_REAL_DATA_KEYWORDS):
        return None
    table_name = select_real_data_table(request)
    return (
        build_fallback_sql_query(request, table_name=table_name),
        f"查询 {table_name} 最近 50 条运行状态、异常码和关键运行指标，用于生成 DCMA 运行报告。",
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
