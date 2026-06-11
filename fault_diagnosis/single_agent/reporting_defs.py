"""Shared definitions for single-agent report generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendMetricDef:
    key: str
    name: str
    unit: str
    group: str
    group_name: str
    precision: int = 2
    warning: float | None = None
    critical: float | None = None


@dataclass
class SqlReportSummary:
    rows: list[dict[str, object]]
    summary: str
    details_markdown: str
    fault_inference: str
    maintenance: str
    chart_payload: str = ""
    health_level: str = "未知"
    data_quality: dict[str, object] | None = None


TREND_METRIC_DEFS = (
    TrendMetricDef("speed_setpoint", "给定转速", "rpm", "speed", "速度跟随"),
    TrendMetricDef("speed_actual", "实际转速", "rpm", "speed", "速度跟随"),
    TrendMetricDef("dc_voltage", "母线电压", "V", "power_supply", "母线电压"),
    TrendMetricDef("motor_temp", "电机温度", "℃", "temperature", "温度", warning=70, critical=85),
    TrendMetricDef("inverter_temp", "变频器温度", "℃", "temperature", "温度", warning=65, critical=80),
    TrendMetricDef("inverter_radiator_temp", "散热器温度", "℃", "temperature", "温度", warning=65, critical=80),
    TrendMetricDef("inverter_load_rate", "变频器负载率", "%", "load", "负载率", warning=75, critical=90),
    TrendMetricDef("motor_load_rate", "电机负载率", "%", "load", "负载率", warning=75, critical=90),
    TrendMetricDef("current_actual", "实际电流", "A", "current", "电流"),
    TrendMetricDef("field_current", "励磁电流", "A", "current", "电流"),
    TrendMetricDef("torque_current", "转矩电流", "A", "current", "电流"),
    TrendMetricDef("motor_power", "电机功率", "kW", "power", "功率"),
    TrendMetricDef("actual_power", "实际功率", "kW", "power", "功率"),
    TrendMetricDef("feedback_power", "反馈功率", "kW", "power", "功率"),
)
TREND_METRICS = tuple(
    (metric.key, f"{metric.name}({metric.unit})" if metric.unit else metric.name)
    for metric in TREND_METRIC_DEFS
)
PRIMARY_LATEST_METRIC_KEYS = {
    "speed_setpoint",
    "speed_actual",
    "dc_voltage",
    "current_actual",
    "motor_temp",
    "inverter_temp",
    "inverter_load_rate",
    "motor_load_rate",
    "motor_power",
}

DATA_FRESHNESS_FRESH_SECONDS = 5 * 60
DATA_FRESHNESS_DELAYED_SECONDS = 60 * 60
SPEED_ERROR_WARNING_PERCENT = 20.0
SPEED_ERROR_CRITICAL_PERCENT = 50.0
MOTOR_TEMP_WARNING = 70.0
MOTOR_TEMP_CRITICAL = 85.0
INVERTER_TEMP_WARNING = 65.0
INVERTER_TEMP_CRITICAL = 80.0
LOAD_WARNING = 75.0
LOAD_CRITICAL = 90.0
DC_VOLTAGE_LOWER = 500.0
DC_VOLTAGE_UPPER = 700.0

CONTROL_WORD_BITS = (
    (0, "ON/OFF1 命令"),
    (1, "OFF2 允许"),
    (2, "OFF3 允许"),
    (3, "运行使能"),
    (4, "斜坡发生器使能"),
    (5, "斜坡发生器启动"),
    (6, "给定值使能"),
    (7, "故障确认"),
    (10, "PLC/远程控制"),
)
STATUS_WORD_BITS = (
    (0, "Ready to switch on"),
    (1, "Ready to operate"),
    (2, "Operation enabled"),
    (3, "Fault present"),
    (4, "OFF2 inactive"),
    (5, "OFF3 inactive"),
    (6, "Switch-on inhibited"),
    (7, "Warning present"),
    (8, "Speed/setpoint deviation in tolerance"),
    (9, "Control requested"),
    (10, "Setpoint reached"),
)
