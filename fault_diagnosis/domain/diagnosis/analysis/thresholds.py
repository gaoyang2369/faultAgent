"""Thresholds for deterministic DCMA runtime diagnosis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricThreshold:
    key: str
    warning: float
    critical: float | None = None
    unit: str = ""
    label: str = ""


SPEED_DEVIATION = MetricThreshold(
    key="speed_deviation_percent",
    warning=20.0,
    critical=50.0,
    unit="%",
    label="速度偏差率",
)
LOAD_RATE = MetricThreshold(
    key="load_rate_percent",
    warning=75.0,
    critical=90.0,
    unit="%",
    label="负载率",
)
MOTOR_TEMPERATURE = MetricThreshold(
    key="motor_temp",
    warning=70.0,
    critical=85.0,
    unit="℃",
    label="电机温度",
)
INVERTER_TEMPERATURE = MetricThreshold(
    key="inverter_temp",
    warning=65.0,
    critical=80.0,
    unit="℃",
    label="变频器/散热器温度",
)
DC_VOLTAGE_LOWER = 500.0
DC_VOLTAGE_UPPER = 700.0

FRESH_SECONDS = 5 * 60
RECENT_SECONDS = 60 * 60
