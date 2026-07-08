"""Shared report and telemetry formatting helpers."""

from __future__ import annotations

from typing import Any

EMPTY_CODE_VALUES = {"", "0", "0.0", "none", "null", "无", "正常", "nan"}


def normalize_code(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in EMPTY_CODE_VALUES else text


def format_value(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"


def format_float(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return format_value(value)


def to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def unique_non_empty(rows: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        value = format_value(row.get(key))
        if value != "-" and value not in values:
            values.append(value)
    return values


def unique_codes(rows: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        code = normalize_code(row.get(key))
        if code and code not in values:
            values.append(code)
    return values


def dedupe_items(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def is_fault_code(code: str) -> bool:
    return str(code or "").strip().upper().startswith("F")


def is_alarm_event_code(code: str) -> bool:
    return str(code or "").strip().upper().startswith("A")


def effective_codes(fault_codes: list[str], alarm_codes: list[str]) -> list[str]:
    return dedupe_items([*fault_codes, *alarm_codes])


def latest_code_streak(rows: list[dict[str, Any]], code: str) -> int:
    target = str(code or "").strip()
    streak = 0
    for row in rows:
        codes = {normalize_code(row.get("fault_code")), normalize_code(row.get("alarm_code"))}
        if target in codes:
            streak += 1
        else:
            break
    return streak


def metric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    chronological_rows = list(reversed(rows))
    return [value for row in chronological_rows if (value := to_float(row.get(key))) is not None]


def metric_max(rows: list[dict[str, Any]], *keys: str) -> float | None:
    values = [value for key in keys for value in metric_values(rows, key)]
    return max(values) if values else None


def speed_deviation(row: dict[str, Any]) -> float | None:
    setpoint = to_float(row.get("speed_setpoint"))
    actual = to_float(row.get("speed_actual"))
    if setpoint is None or actual is None or abs(setpoint) < 1:
        return None
    return abs(actual - setpoint) / max(abs(setpoint), 1)


def speed_deviation_percent(row: dict[str, Any]) -> float | None:
    deviation = speed_deviation(row)
    return round(deviation * 100, 2) if deviation is not None else None


def is_abnormal_row(row: dict[str, Any]) -> bool:
    return bool(normalize_code(row.get("fault_code")) or normalize_code(row.get("alarm_code")))
