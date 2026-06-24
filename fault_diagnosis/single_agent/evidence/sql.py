"""SQL-result evidence construction for the restricted single-agent runtime."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ...diagnosis.contracts import DiagnosisRequest, EvidenceItem, EvidenceQuality, SqlStepArtifact
from ..reporting.utils import (
    format_float as _format_float,
    format_value as _format_value,
    is_abnormal_row as _is_abnormal_row,
    metric_max as _metric_max,
    metric_values as _metric_values,
    normalize_code as _normalize_code,
    speed_deviation_percent as _speed_deviation_percent,
    to_float as _to_float,
    unique_codes as _unique_codes,
    unique_non_empty as _unique_values,
)
from ..sql_result_parser import parse_sql_rows
from ..sql_safety import REAL_DATA_LATEST_TABLE
from .utils import dedupe, first_non_empty

_FRESH_SECONDS = 5 * 60
_RECENT_SECONDS = 60 * 60
_SPEED_WARNING_PERCENT = 20.0
_LOAD_WARNING = 75.0
_MOTOR_TEMP_WARNING = 70.0
_INVERTER_TEMP_WARNING = 65.0


def build_sql_evidence_items(
    sql_artifact: SqlStepArtifact,
    *,
    request: DiagnosisRequest | None,
) -> list[EvidenceItem]:
    """Build evidence items from normalized SQL tool output."""

    rows = parse_sql_rows(sql_artifact.raw_output or sql_artifact.result_preview)
    if not sql_artifact.success or not rows:
        return [
            EvidenceItem(
                evidence_id="ev_sql_result_missing",
                evidence_type="tool_error" if sql_artifact.error else "device_status",
                source_type="sql",
                source_name=REAL_DATA_LATEST_TABLE,
                asset_id=request.equipment_hint if request else None,
                content={
                    "summary": sql_artifact.summary,
                    "error": sql_artifact.error,
                    "sql_used": sql_artifact.sql_used,
                    "result_preview": sql_artifact.result_preview,
                },
                summary=sql_artifact.error or sql_artifact.summary or "SQL 未返回可解析运行数据。",
                quality=EvidenceQuality(reliability="medium", freshness="unknown", relevance="medium", completeness="missing"),
                metadata={"sql_used": sql_artifact.sql_used},
                title="SQL 查询结果",
                importance="low",
            )
        ]

    latest = rows[0]
    devices = _unique_values(rows, "device_name")
    asset_id = first_non_empty([request.equipment_hint if request else None, *devices])
    latest_time = _format_value(latest.get("create_time"))
    oldest_time = _format_value(rows[-1].get("create_time"))
    fault_codes = _unique_codes(rows, "fault_code")
    alarm_codes = _unique_codes(rows, "alarm_code")
    effective_codes = dedupe([*fault_codes, *alarm_codes])
    abnormal_count = sum(1 for row in rows if _is_abnormal_row(row))
    items = [
        EvidenceItem(
            evidence_id="ev_sql_sample_window",
            evidence_type="device_status",
            source_type="sql",
            source_name=REAL_DATA_LATEST_TABLE,
            asset_id=asset_id,
            timestamp=latest_time if latest_time != "-" else None,
            time_range={"start": oldest_time, "end": latest_time} if oldest_time != "-" and latest_time != "-" else None,
            content={
                "sample_count": len(rows),
                "device_names": devices,
                "latest_status": _format_value(latest.get("status")),
                "latest_sample_time": latest_time,
                "oldest_sample_time": oldest_time,
                "source_table": REAL_DATA_LATEST_TABLE,
            },
            summary=(
                f"SQL 返回 {len(rows)} 条 {REAL_DATA_LATEST_TABLE} 运行记录，"
                f"最新时间 {latest_time}，设备 {', '.join(devices) or asset_id or '未识别'}。"
            ),
            quality=EvidenceQuality(
                reliability="high",
                freshness=_freshness_from_timestamp(latest_time),
                relevance="high",
                completeness="complete",
            ),
            metadata={"sql_used": sql_artifact.sql_used, "table": REAL_DATA_LATEST_TABLE},
            title="SQL 样本窗口",
            importance="high",
        )
    ]
    if effective_codes:
        items.append(
            EvidenceItem(
                evidence_id="ev_sql_event_codes",
                evidence_type="alarm_event",
                source_type="sql",
                source_name=REAL_DATA_LATEST_TABLE,
                asset_id=asset_id,
                timestamp=latest_time if latest_time != "-" else None,
                time_range={"start": oldest_time, "end": latest_time} if oldest_time != "-" and latest_time != "-" else None,
                content={
                    "fault_codes": fault_codes,
                    "alarm_codes": alarm_codes,
                    "effective_codes": effective_codes,
                    "abnormal_count": abnormal_count,
                    "sample_count": len(rows),
                },
                summary=(
                    f"样本窗口内 {abnormal_count}/{len(rows)} 条记录包含有效事件码/告警码："
                    f"{', '.join(effective_codes)}。"
                ),
                quality=EvidenceQuality(reliability="high", freshness=_freshness_from_timestamp(latest_time), relevance="high", completeness="complete"),
                metadata={"columns": ["fault_code", "alarm_code"], "table": REAL_DATA_LATEST_TABLE},
                title="SQL 异常码统计",
                importance="high",
            )
        )

    items.extend(_sql_metric_evidence(rows, asset_id=asset_id, latest_time=latest_time))
    return items


def _sql_metric_evidence(rows: list[dict[str, Any]], *, asset_id: str | None, latest_time: str) -> list[EvidenceItem]:
    latest = rows[0]
    items: list[EvidenceItem] = []
    speed_deviation = _speed_deviation_percent(latest)
    if speed_deviation is not None:
        status = "abnormal" if speed_deviation >= _SPEED_WARNING_PERCENT else "normal"
        items.append(
            EvidenceItem(
                evidence_id="ev_sql_speed_deviation",
                evidence_type="timeseries_feature",
                source_type="sql",
                source_name=REAL_DATA_LATEST_TABLE,
                asset_id=asset_id,
                timestamp=latest_time if latest_time != "-" else None,
                content={
                    "metric": "speed_deviation_percent",
                    "value": speed_deviation,
                    "unit": "%",
                    "threshold": _SPEED_WARNING_PERCENT,
                    "status": status,
                },
                summary=(
                    f"最新速度偏差率 {speed_deviation:g}%，"
                    f"{'超过' if status == 'abnormal' else '未超过'}关注阈值 {_SPEED_WARNING_PERCENT:g}%。"
                ),
                quality=EvidenceQuality(reliability="high", freshness=_freshness_from_timestamp(latest_time), relevance="high", completeness="complete"),
                metadata={"formula": "|speed_setpoint-speed_actual| / max(|speed_setpoint|, 1)"},
                title="速度偏差特征",
                importance="high" if status == "abnormal" else "medium",
            )
        )

    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate")
    if max_load is not None:
        status = "abnormal" if max_load >= _LOAD_WARNING else "normal"
        items.append(
            EvidenceItem(
                evidence_id="ev_sql_load_level",
                evidence_type="metric_snapshot",
                source_type="sql",
                source_name=REAL_DATA_LATEST_TABLE,
                asset_id=asset_id,
                timestamp=latest_time if latest_time != "-" else None,
                content={
                    "metric": "max_load_rate",
                    "value": round(max_load, 2),
                    "unit": "%",
                    "threshold": _LOAD_WARNING,
                    "status": status,
                },
                summary=(
                    f"样本窗口最高负载率 {_format_float(max_load)}%，"
                    f"{'进入' if status == 'abnormal' else '未进入'}关注区间。"
                ),
                quality=EvidenceQuality(reliability="high", freshness=_freshness_from_timestamp(latest_time), relevance="high", completeness="complete"),
                metadata={"columns": ["inverter_load_rate", "motor_load_rate"]},
                title="负载率快照",
                importance="high" if status == "abnormal" else "medium",
            )
        )

    max_motor_temp = _metric_max(rows, "motor_temp")
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp")
    if max_motor_temp is not None or max_inverter_temp is not None:
        temp_status = (
            "abnormal"
            if (max_motor_temp or 0) >= _MOTOR_TEMP_WARNING or (max_inverter_temp or 0) >= _INVERTER_TEMP_WARNING
            else "normal"
        )
        items.append(
            EvidenceItem(
                evidence_id="ev_sql_temperature_level",
                evidence_type="metric_snapshot",
                source_type="sql",
                source_name=REAL_DATA_LATEST_TABLE,
                asset_id=asset_id,
                timestamp=latest_time if latest_time != "-" else None,
                content={
                    "motor_temp_max": round(max_motor_temp, 2) if max_motor_temp is not None else None,
                    "inverter_temp_max": round(max_inverter_temp, 2) if max_inverter_temp is not None else None,
                    "unit": "℃",
                    "motor_threshold": _MOTOR_TEMP_WARNING,
                    "inverter_threshold": _INVERTER_TEMP_WARNING,
                    "status": temp_status,
                },
                summary=(
                    f"样本窗口电机最高温度 {_format_float(max_motor_temp)}℃，"
                    f"变频器最高温度 {_format_float(max_inverter_temp)}℃。"
                ),
                quality=EvidenceQuality(reliability="high", freshness=_freshness_from_timestamp(latest_time), relevance="high", completeness="complete"),
                metadata={"columns": ["motor_temp", "inverter_temp", "inverter_radiator_temp"]},
                title="温度快照",
                importance="high" if temp_status == "abnormal" else "medium",
            )
        )
    return items


def _freshness_from_timestamp(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "unknown"
    delta_seconds = abs((datetime.now() - parsed).total_seconds())
    if delta_seconds <= _FRESH_SECONDS:
        return "current"
    if delta_seconds <= _RECENT_SECONDS:
        return "recent"
    return "stale"


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    normalized = text.replace("T", " ").replace("/", "-").strip()
    normalized = re.sub(r"\s+\d{1,3}ms$", "", normalized)
    normalized = re.sub(r"\.\d+$", "", normalized)
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y年%m月%d日 %H:%M:%S",
        "%Y年%m月%d日 %H:%M",
    ):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    return None
