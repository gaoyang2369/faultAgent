"""Report payload and final-answer formatting helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from .operation import build_operation_diagnosis_report
from .defs import (
    DATA_FRESHNESS_DELAYED_SECONDS as _DATA_FRESHNESS_DELAYED_SECONDS,
    DATA_FRESHNESS_FRESH_SECONDS as _DATA_FRESHNESS_FRESH_SECONDS,
    INVERTER_TEMP_CRITICAL as _INVERTER_TEMP_CRITICAL,
    INVERTER_TEMP_WARNING as _INVERTER_TEMP_WARNING,
    LOAD_CRITICAL as _LOAD_CRITICAL,
    LOAD_WARNING as _LOAD_WARNING,
    MOTOR_TEMP_CRITICAL as _MOTOR_TEMP_CRITICAL,
    MOTOR_TEMP_WARNING as _MOTOR_TEMP_WARNING,
    PRIMARY_LATEST_METRIC_KEYS as _PRIMARY_LATEST_METRIC_KEYS,
    SPEED_ERROR_CRITICAL_PERCENT as _SPEED_ERROR_CRITICAL_PERCENT,
    SPEED_ERROR_WARNING_PERCENT as _SPEED_ERROR_WARNING_PERCENT,
    TREND_METRIC_DEFS as _TREND_METRIC_DEFS,
    SqlReportSummary,
)
from .utils import (
    dedupe_items as _dedupe_items,
    effective_codes as _effective_codes,
    format_float as _format_float,
    format_value as _format_value,
    is_alarm_event_code as _is_alarm_event_code,
    is_abnormal_row as _is_abnormal_row,
    is_fault_code as _is_fault_code,
    latest_code_streak as _latest_code_streak,
    metric_max as _metric_max,
    metric_values as _metric_values,
    normalize_code as _normalize_code,
    speed_deviation as _speed_deviation,
    speed_deviation_percent as _speed_deviation_percent,
    to_float as _to_float,
    unique_codes as _unique_codes,
    unique_non_empty as _unique_non_empty,
)
from ..sql_result_parser import parse_sql_rows
from ..sql_safety import REAL_DATA_LATEST_TABLE

_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9._\-]+\.(?:md|html))", re.IGNORECASE)
_STATUS_REPORT_HINTS = ("运行状态", "状态报告", "当前状态", "运行情况", "运行报告", "巡检", "当前运行")
_DIAGNOSIS_REPORT_HINTS = ("故障诊断", "故障原因", "根因", "维修", "维修方案", "怎么处理", "处置建议")
_KNOWLEDGE_ACTION_LABELS = (
    "含义",
    "说明",
    "反应",
    "原因",
    "触发",
    "处理",
    "排除",
    "措施",
    "检查",
    "维修",
    "复位",
)
_KNOWLEDGE_SOURCE_PREFIXES = (
    "来源",
    "source_type",
    "file_id",
    "extract_backend",
    "corrected",
    "correction_source",
    "检索方式",
    "故障码",
)

def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def _is_status_report_request(request: DiagnosisRequest) -> bool:
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
    if "运行状态报告" in text or "当前运行状态报告" in text:
        return True
    if _has_any(text, _STATUS_REPORT_HINTS) and not request.fault_code_hint:
        return True
    if request.fault_code_hint and _has_any(text, _DIAGNOSIS_REPORT_HINTS):
        return False
    return False


def _report_title_and_type(request: DiagnosisRequest) -> tuple[str, str]:
    if _is_status_report_request(request):
        return "DCMA 运行诊断报告", "运行诊断报告"
    if request.fault_code_hint:
        return "DCMA 故障诊断报告", f"{request.fault_code_hint} 故障诊断"
    return "DCMA 故障诊断报告", "故障诊断"


def _code_severity(fault_codes: list[str], alarm_codes: list[str]) -> str:
    codes = _effective_codes(fault_codes, alarm_codes)
    if any(_is_fault_code(code) for code in codes):
        return "fault"
    if codes:
        return "warning"
    return "normal"


def _code_label(fault_codes: list[str], alarm_codes: list[str]) -> str:
    severity = _code_severity(fault_codes, alarm_codes)
    if severity == "fault":
        return "故障码"
    if severity == "warning":
        return "事件码/告警码"
    return "异常码"


def _parse_datetime(value: object) -> datetime | None:
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


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "未评估"
    seconds = max(0, float(seconds))
    if seconds < 60:
        return f"{_format_float(seconds, 0)} 秒"
    minutes = seconds / 60
    if minutes < 60:
        return f"{_format_float(minutes, 1)} 分钟"
    hours = minutes / 60
    if hours < 48:
        return f"{_format_float(hours, 1)} 小时"
    days = hours / 24
    return f"{_format_float(days, 1)} 天"


def _data_freshness_label(freshness_seconds: float | None) -> tuple[str, str]:
    if freshness_seconds is None:
        return "未知", "缺少可解析报告时间或最新采样时间"
    if freshness_seconds <= _DATA_FRESHNESS_FRESH_SECONDS:
        return "实时性良好", "可作为当前状态的强参考"
    if freshness_seconds <= _DATA_FRESHNESS_DELAYED_SECONDS:
        return "轻微滞后", "可用于近况判断，关键操作前仍需现场复核"
    return "已滞后", "仅代表采样窗口，不宜直接视为实时状态"


def _metric_missing_summary(rows: list[dict[str, object]]) -> tuple[int, int, str]:
    total = len(rows) * len(_TREND_METRIC_DEFS)
    if total <= 0:
        return 0, 0, "0%"
    missing = 0
    for row in rows:
        for metric in _TREND_METRIC_DEFS:
            if _to_float(row.get(metric.key)) is None:
                missing += 1
    return missing, total, _format_percent(total - missing, total)


def _build_data_quality(rows: list[dict[str, object]], *, report_time: str | None = None) -> dict[str, object]:
    chronological_times = [
        parsed
        for row in reversed(rows)
        if (parsed := _parse_datetime(_row_time(row))) is not None
    ]
    latest_time = chronological_times[-1] if chronological_times else None
    oldest_time = chronological_times[0] if chronological_times else None
    intervals = [
        (right - left).total_seconds()
        for left, right in zip(chronological_times, chronological_times[1:])
        if (right - left).total_seconds() >= 0
    ]
    report_dt = _parse_datetime(report_time) if report_time else None
    freshness_seconds = (
        (report_dt - latest_time).total_seconds()
        if report_dt is not None and latest_time is not None
        else None
    )
    freshness_label, currentness = _data_freshness_label(freshness_seconds)
    missing, total, availability = _metric_missing_summary(rows)
    average_interval = sum(intervals) / len(intervals) if intervals else None
    max_interval = max(intervals) if intervals else None
    return {
        "sample_count": len(rows),
        "oldest_sample_time": oldest_time.strftime("%Y-%m-%d %H:%M:%S") if oldest_time else "-",
        "latest_sample_time": latest_time.strftime("%Y-%m-%d %H:%M:%S") if latest_time else "-",
        "average_sample_interval_seconds": round(average_interval, 2) if average_interval is not None else None,
        "max_sample_gap_seconds": round(max_interval, 2) if max_interval is not None else None,
        "freshness_seconds": round(freshness_seconds, 2) if freshness_seconds is not None else None,
        "freshness_label": freshness_label,
        "currentness": currentness,
        "metric_value_count": total,
        "missing_metric_value_count": missing,
        "metric_availability": availability,
    }


def _parse_sql_rows(raw_output: str) -> list[dict[str, object]]:
    return parse_sql_rows(raw_output)


def _count_rows(rows: list[dict[str, object]], key: str, *, normalize_code: bool = False) -> list[list[str]]:
    counts: dict[str, int] = {}
    for row in rows:
        value = _normalize_code(row.get(key)) if normalize_code else _format_value(row.get(key))
        if not value or value == "-":
            continue
        counts[value] = counts.get(value, 0) + 1
    return [[value, str(count)] for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _count_abnormal_codes(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for row in rows:
        for key in ("fault_code", "alarm_code"):
            value = _normalize_code(row.get(key))
            if value:
                counts[value] = counts.get(value, 0) + 1
    return [
        {"name": value, "value": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0%"
    value = numerator / denominator * 100
    return f"{f'{value:.1f}'.rstrip('0').rstrip('.')}%"


def _row_time(row: dict[str, object]) -> str:
    return _format_value(row.get("create_time") or row.get("timestamp"))


def _latest_abnormal_streak(rows: list[dict[str, object]]) -> int:
    streak = 0
    for row in rows:
        if not _is_abnormal_row(row):
            break
        streak += 1
    return streak


def _status_level(rows: list[dict[str, object]], fault_codes: list[str], alarm_codes: list[str]) -> str:
    if not rows:
        return "未知"
    severity = _code_severity(fault_codes, alarm_codes)
    latest = rows[0]
    speed_error = _speed_deviation_percent(latest)
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
    max_motor_temp = _metric_max(rows, "motor_temp") or 0
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp") or 0
    if severity == "fault":
        return "故障 / 需处理"
    if severity == "warning":
        return "告警 / 需确认"
    if (
        (speed_error is not None and speed_error >= _SPEED_ERROR_CRITICAL_PERCENT)
        or max_load >= _LOAD_CRITICAL
        or max_motor_temp >= _MOTOR_TEMP_CRITICAL
        or max_inverter_temp >= _INVERTER_TEMP_CRITICAL
    ):
        return "告警 / 需确认"
    if (
        (speed_error is not None and speed_error >= _SPEED_ERROR_WARNING_PERCENT)
        or max_load >= _LOAD_WARNING
        or max_motor_temp >= _MOTOR_TEMP_WARNING
        or max_inverter_temp >= _INVERTER_TEMP_WARNING
    ):
        return "关注 / 需观察"
    return "正常 / 持续观察"


def _status_priority(status_level: str) -> str:
    if "故障" in status_level:
        return "高"
    if "告警" in status_level:
        return "中"
    if "关注" in status_level:
        return "低-中"
    return "低"


def _current_event_text(fault_codes: list[str], alarm_codes: list[str]) -> str:
    codes = _effective_codes(fault_codes, alarm_codes)
    return ", ".join(codes) if codes else "无有效异常码"


def _key_phenomenon(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "缺少可解析运行数据"
    latest = rows[0]
    phenomena: list[str] = []
    speed_error = _speed_deviation_percent(latest)
    if speed_error is not None and speed_error >= _SPEED_ERROR_WARNING_PERCENT:
        phenomena.append(f"速度给定与实际速度偏差 {speed_error:g}%")
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
    if max_load >= _LOAD_WARNING:
        phenomena.append(f"最高负载率 {_format_float(max_load)}%")
    max_motor_temp = _metric_max(rows, "motor_temp") or 0
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp") or 0
    if max_motor_temp >= _MOTOR_TEMP_WARNING or max_inverter_temp >= _INVERTER_TEMP_WARNING:
        phenomena.append(f"温度进入关注区间，电机最高 {_format_float(max_motor_temp)}℃，变频器最高 {_format_float(max_inverter_temp)}℃")
    return "；".join(phenomena) if phenomena else "关键运行指标未触发预设关注阈值"


def _initial_assessment(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
) -> str:
    codes = _effective_codes(fault_codes, alarm_codes)
    if any(_is_alarm_event_code(code) for code in codes) and not any(_is_fault_code(code) for code in codes):
        return "存在参数/配置/调试相关事件迹象，同时需结合运行模式、状态字和关键指标确认是否影响当前运行。"
    if any(_is_fault_code(code) for code in codes):
        return "存在 F 类故障码，需按现场安全规程和厂家手册完成原因确认与复位验证。"
    if rows and _speed_deviation_percent(rows[0]) is not None and (_speed_deviation_percent(rows[0]) or 0) >= _SPEED_ERROR_WARNING_PERCENT:
        return "未检出有效异常码，但速度跟随存在明显偏差，需确认运行命令、限幅和反馈链路。"
    return "当前样本未显示显著异常，建议继续按采样窗口观察关键指标。"


def _next_action(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
) -> str:
    codes = _effective_codes(fault_codes, alarm_codes)
    if any(_is_fault_code(code) for code in codes):
        return "先确认现场安全和故障保持状态，再按手册复核触发条件、复位条件和复位后采样变化。"
    if codes:
        return "先确认数据源、设备映射、运行/调试模式和近期参数变更，再复核单位制及功能块配置。"
    if rows and (_speed_deviation_percent(rows[0]) or 0) >= _SPEED_ERROR_WARNING_PERCENT:
        return "优先核对速度给定来源、运行使能、限速/限流和反馈链路。"
    return "保持观察，继续跟踪状态字、速度、电流、温度和负载率。"


def _build_status_summary(rows: list[dict[str, object]], data_quality: dict[str, object] | None = None) -> dict[str, object]:
    if not rows:
        return {
            "status_level": "未知",
            "source_table": REAL_DATA_LATEST_TABLE,
            "latest_sample_time": "-",
            "device": "未识别",
            "sample_window": "无可解析样本",
            "current_event": "未知",
            "key_phenomenon": "SQL 未返回可解析运行数据",
            "initial_assessment": "无法基于数据库行数据确认当前采样窗口状态。",
            "priority": "未知",
            "next_action": f"先确认 {REAL_DATA_LATEST_TABLE} 最新数据是否可查询。",
        }
    fault_codes = _unique_codes(rows, "fault_code")
    alarm_codes = _unique_codes(rows, "alarm_code")
    devices = _unique_non_empty(rows, "device_name")
    latest = rows[0]
    latest_time = _format_value(latest.get("create_time"))
    oldest_time = _row_time(rows[-1])
    status_level = _status_level(rows, fault_codes, alarm_codes)
    quality = data_quality or _build_data_quality(rows)
    return {
        "status_level": status_level,
        "source_table": REAL_DATA_LATEST_TABLE,
        "latest_sample_time": latest_time,
        "device": ", ".join(devices) or "未识别",
        "device_mapping": f"DCMA -> {', '.join(devices)}" if devices else "DCMA -> 未识别数据来源设备",
        "sample_window": f"{oldest_time} 至 {latest_time}，{len(rows)} 条记录",
        "current_event": _current_event_text(fault_codes, alarm_codes),
        "key_phenomenon": _key_phenomenon(rows),
        "initial_assessment": _initial_assessment(rows, fault_codes, alarm_codes),
        "priority": _status_priority(status_level),
        "next_action": _next_action(rows, fault_codes, alarm_codes),
        "freshness_label": quality.get("freshness_label") or "未知",
        "currentness": quality.get("currentness") or "-",
    }


def _event_timeline_items(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    counts = _count_abnormal_codes(rows)
    if not counts:
        return []
    return [
        {
            "code": str(item["name"]),
            "count": int(item["value"]),
            "sample_count": len(rows),
            "ratio": _format_percent(int(item["value"]), len(rows)),
            "latest_streak": _latest_code_streak(rows, str(item["name"])),
            "continuity": "持续存在" if int(item["value"]) == len(rows) else "间歇出现",
        }
        for item in counts
    ]


def _load_signal(rows: list[dict[str, object]]) -> str:
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate")
    if max_load is None:
        return "缺少负载率数据"
    if max_load >= 90:
        return f"最高负载率 {_format_float(max_load)}%，存在高负载风险"
    if max_load >= 75:
        return f"最高负载率 {_format_float(max_load)}%，建议关注负载裕量"
    return f"最高负载率 {_format_float(max_load)}%，未见高负载特征"


def _temperature_signal(rows: list[dict[str, object]]) -> str:
    max_motor_temp = _metric_max(rows, "motor_temp")
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp")
    if max_motor_temp is None and max_inverter_temp is None:
        return "缺少温度数据"
    motor_text = f"电机最高 {_format_float(max_motor_temp)}" if max_motor_temp is not None else "电机温度缺失"
    inverter_text = (
        f"变频器最高 {_format_float(max_inverter_temp)}"
        if max_inverter_temp is not None
        else "变频器温度缺失"
    )
    if (max_motor_temp or 0) >= 80 or (max_inverter_temp or 0) >= 70:
        suffix = "，温度偏高，需检查散热和负载"
    elif (max_motor_temp or 0) >= 60 or (max_inverter_temp or 0) >= 50:
        suffix = "，温度进入关注区间"
    else:
        suffix = "，未见温度高位特征"
    return f"{motor_text}，{inverter_text}{suffix}"


def _dc_voltage_signal(rows: list[dict[str, object]]) -> str:
    values = _metric_values(rows, "dc_voltage")
    if not values:
        return "缺少母线电压数据"
    average = sum(values) / len(values)
    span = max(values) - min(values)
    fluctuation = span / average * 100 if average else 0
    suffix = "波动较明显" if fluctuation >= 5 else "波动较小"
    return (
        f"范围 {_format_float(min(values))}-{_format_float(max(values))} V，"
        f"波动 {_format_float(fluctuation)}%，{suffix}"
    )


def _speed_signal(latest: dict[str, object]) -> str:
    setpoint = _to_float(latest.get("speed_setpoint"))
    actual = _to_float(latest.get("speed_actual"))
    if setpoint is None or actual is None:
        return "缺少速度给定或反馈数据"
    if abs(setpoint) < 1 and abs(actual) < 1:
        return "给定与反馈均接近 0，呈停机/待机特征"
    deviation = _speed_deviation(latest)
    if deviation is None:
        return f"给定 {_format_float(setpoint)}，实际 {_format_float(actual)}"
    suffix = "偏差较大，需核对速度闭环和运行命令" if deviation >= 0.2 else "偏差处于可观察范围"
    return f"给定 {_format_float(setpoint)}，实际 {_format_float(actual)}，偏差 {_format_percent(round(deviation * 1000), 1000)}，{suffix}"


def _build_signal_rows(rows: list[dict[str, object]], fault_codes: list[str], alarm_codes: list[str]) -> list[list[str]]:
    latest = rows[0]
    abnormal_count = sum(1 for row in rows if _is_abnormal_row(row))
    latest_streak = _latest_abnormal_streak(rows)
    code_text = ", ".join(fault_codes + alarm_codes) if fault_codes or alarm_codes else "无"
    return [
        [
            "异常持续性",
            f"{abnormal_count}/{len(rows)} 条记录含有效异常码，最新连续 {latest_streak} 条",
            f"核心异常码：{code_text}" if code_text != "无" else "样本内未见有效异常码",
        ],
        ["速度跟随", _speed_signal(latest), "用于判断给定、反馈、运行使能和负载变化是否一致"],
        ["温度状态", _temperature_signal(rows), "用于排查散热、过载和环境温度影响"],
        ["负载状态", _load_signal(rows), "用于识别机械卡滞、工艺负载突变或参数不匹配"],
        ["母线电压", _dc_voltage_signal(rows), "用于观察供电侧稳定性和直流母线异常波动"],
    ]


def _derive_health_level(rows: list[dict[str, object]], fault_codes: list[str], alarm_codes: list[str]) -> str:
    latest = rows[0]
    severity = _code_severity(fault_codes, alarm_codes)
    if severity == "fault":
        return "故障：检测到 F 类故障码"
    if severity == "warning":
        return "告警 / 需确认：检测到事件码或告警码"
    speed_deviation = _speed_deviation(latest)
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
    max_motor_temp = _metric_max(rows, "motor_temp") or 0
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp") or 0
    if (speed_deviation is not None and speed_deviation >= 0.2) or max_load >= 75 or max_motor_temp >= 60 or max_inverter_temp >= 50:
        return "需关注：关键指标存在偏离或接近关注区间"
    return "未见显著异常：样本内未发现有效异常码"


def _build_group_thresholds(metrics: list[dict[str, object]]) -> list[dict[str, object]]:
    thresholds: list[dict[str, object]] = []
    seen: set[tuple[str, float, str]] = set()
    for metric in metrics:
        definition = next((item for item in _TREND_METRIC_DEFS if item.key == metric.get("key")), None)
        warning = definition.warning if definition is not None else _to_float(metric.get("warning"))
        critical = definition.critical if definition is not None else _to_float(metric.get("critical"))
        unit = definition.unit if definition is not None else str(metric.get("unit") or "")
        for level, value in (("关注", warning), ("高风险", critical)):
            if value is None:
                continue
            key = (unit, value, level)
            if key in seen:
                continue
            seen.add(key)
            thresholds.append({"name": level, "value": value, "unit": unit})
    return thresholds


def _build_trend_groups(trend_metrics: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    grouped: dict[str, list[dict[str, object]]] = {}
    group_names: dict[str, str] = {}
    for metric in trend_metrics:
        group = str(metric.get("group") or "default")
        grouped.setdefault(group, []).append(metric)
        group_names[group] = str(metric.get("group_name") or "关键指标")
    for group, metrics in grouped.items():
        groups.append(
            {
                "key": group,
                "name": group_names[group],
                "metrics": metrics,
                "thresholds": _build_group_thresholds(metrics),
            }
        )
    return groups


def _build_latest_metric_groups(latest_metrics: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    grouped: dict[str, list[dict[str, object]]] = {}
    group_names: dict[str, str] = {}
    for metric in latest_metrics:
        group = str(metric.get("group") or "default")
        grouped.setdefault(group, []).append(metric)
        group_names[group] = str(metric.get("group_name") or "关键指标")
    for group, metrics in grouped.items():
        groups.append({"key": group, "name": group_names[group], "metrics": metrics})
    return groups


def _build_health_overview_group(trend_metrics: list[dict[str, object]]) -> dict[str, object] | None:
    overview_keys = {
        "dc_voltage",
        "current_actual",
        "motor_temp",
        "inverter_temp",
        "inverter_radiator_temp",
        "inverter_load_rate",
        "motor_load_rate",
    }
    metrics = [metric for metric in trend_metrics if str(metric.get("key")) in overview_keys]
    if not metrics:
        return None
    return {
        "key": "health_overview",
        "name": "温度 / 电气健康概览",
        "metrics": metrics,
        "thresholds": _build_group_thresholds(metrics),
        "is_diagnostic_overview": True,
    }


def _build_chart_payload(rows: list[dict[str, object]], *, data_quality: dict[str, object] | None = None) -> str:
    chronological_rows = list(reversed(rows))
    timestamps = [_row_time(row) for row in chronological_rows]
    trend_metrics = []
    for metric in _TREND_METRIC_DEFS:
        values = []
        for row in chronological_rows:
            value = _to_float(row.get(metric.key))
            values.append(round(value, metric.precision) if value is not None else None)
        if any(value is not None for value in values):
            trend_metrics.append(
                {
                    "key": metric.key,
                    "name": metric.name,
                    "unit": metric.unit,
                    "group": metric.group,
                    "group_name": metric.group_name,
                    "values": values,
                    "warning": metric.warning,
                    "critical": metric.critical,
                }
            )
    speed_error_values = []
    for row in chronological_rows:
        speed_error = _speed_deviation_percent(row)
        speed_error_values.append(speed_error if speed_error is not None else None)
    if any(value is not None for value in speed_error_values):
        trend_metrics.append(
            {
                "key": "speed_error_rate",
                "name": "速度偏差率",
                "unit": "%",
                "group": "speed",
                "group_name": "速度跟随",
                "values": speed_error_values,
                "warning": _SPEED_ERROR_WARNING_PERCENT,
                "critical": _SPEED_ERROR_CRITICAL_PERCENT,
                }
            )

    trend_groups = _build_trend_groups(trend_metrics)
    health_overview_group = _build_health_overview_group(trend_metrics)
    if health_overview_group is not None:
        trend_groups.insert(1 if trend_groups else 0, health_overview_group)

    def count_payload(key: str, *, normalize_code: bool = False) -> list[dict[str, object]]:
        return [
            {"name": name, "value": int(count)}
            for name, count in _count_rows(rows, key, normalize_code=normalize_code)
        ]

    quality = data_quality or _build_data_quality(rows)
    latest = rows[0]
    latest_metrics = [
        {
            "key": metric.key,
            "name": metric.name,
            "value": round(value, metric.precision),
            "unit": metric.unit,
            "group": metric.group,
            "group_name": metric.group_name,
            "warning": metric.warning,
            "critical": metric.critical,
        }
        for metric in _TREND_METRIC_DEFS
        if metric.key in _PRIMARY_LATEST_METRIC_KEYS and (value := _to_float(latest.get(metric.key))) is not None
    ]
    payload = {
        "source_table": REAL_DATA_LATEST_TABLE,
        "timestamps": timestamps,
        "trend_metrics": trend_metrics,
        "trend_groups": trend_groups,
        "status_counts": count_payload("status"),
        "fault_counts": _count_abnormal_codes(rows),
        "event_timeline": _event_timeline_items(rows),
        "latest_metrics": latest_metrics,
        "latest_metric_groups": _build_latest_metric_groups(latest_metrics),
        "data_quality": quality,
        "status_summary": _build_status_summary(rows, quality),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _knowledge_blocks(raw_output: str) -> list[str]:
    return [block.strip() for block in (raw_output or "").split("\n\n") if block.strip()]


def _line_has_knowledge_action(line: str) -> bool:
    normalized = line.strip()
    return any(label in normalized for label in _KNOWLEDGE_ACTION_LABELS)


def _is_source_metadata_line(line: str) -> bool:
    normalized = line.strip()
    return any(normalized.startswith(prefix) for prefix in _KNOWLEDGE_SOURCE_PREFIXES)


def _knowledge_action_summaries(
    knowledge_artifact: KnowledgeStepArtifact,
    codes: list[str],
    *,
    per_code_limit: int = 4,
) -> list[str]:
    raw_output = (knowledge_artifact.raw_output or "").strip()
    if not raw_output or not knowledge_artifact.success:
        return []

    blocks = _knowledge_blocks(raw_output)
    summaries: list[str] = []
    normalized_codes = [code.upper() for code in codes if code]
    target_codes = normalized_codes or [""]

    for code in target_codes:
        matched_blocks = [
            block
            for block in blocks
            if not code or code in block.upper()
        ]
        selected_lines: list[str] = []
        for block in matched_blocks:
            for raw_line in block.splitlines():
                line = raw_line.strip()
                if not line or _is_source_metadata_line(line):
                    continue
                if _line_has_knowledge_action(line) or (code and code in line.upper()):
                    if line not in selected_lines:
                        selected_lines.append(line)
                if len(selected_lines) >= per_code_limit:
                    break
            if len(selected_lines) >= per_code_limit:
                break
        if selected_lines:
            prefix = f"{code}：" if code else ""
            summaries.append(f"{prefix}{'；'.join(selected_lines[:per_code_limit])}")
    return summaries


def build_knowledge_action_summaries(
    knowledge_artifact: KnowledgeStepArtifact,
    codes: list[str],
    *,
    per_code_limit: int = 4,
) -> list[str]:
    return _knowledge_action_summaries(knowledge_artifact, codes, per_code_limit=per_code_limit)


def _build_probable_cause_items(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
    *,
    knowledge_artifact: KnowledgeStepArtifact,
) -> list[str]:
    items: list[str] = []
    code_summaries = _knowledge_action_summaries(knowledge_artifact, fault_codes + alarm_codes)
    severity = _code_severity(fault_codes, alarm_codes)
    if code_summaries:
        prefix = "故障码主因优先按 RAG 手册核对" if severity == "fault" else "事件码解释优先按 RAG 手册核对"
        items.extend(f"{prefix}：{summary}" for summary in code_summaries[:3])
    elif fault_codes or alarm_codes:
        items.append("数据库已检出有效事件码/告警码，但知识库未命中明确释义；原因需结合厂家手册、参数记录和现场现象确认。")

    if not rows:
        return _dedupe_items(items)

    latest = rows[0]
    speed_deviation = _speed_deviation(latest)
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
    max_motor_temp = _metric_max(rows, "motor_temp") or 0
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp") or 0

    if speed_deviation is not None and speed_deviation >= 0.2:
        items.append("速度给定与反馈偏差较大，可能关联运行使能、给定源、反馈链路或负载扰动；该项需作为并发问题验证，不能直接等同于异常码根因。")
    if max_load >= 75:
        items.append("负载率进入关注区间，可能关联机械传动、工艺负载、制动状态或参数限幅；需结合现场负载变化验证。")
    if max_motor_temp >= 60 or max_inverter_temp >= 50:
        items.append("温度进入关注区间，可能关联散热条件、连续负载或环境温度；需结合风道、柜内温度和负载历史验证。")
    return _dedupe_items(items)


def _build_verification_items(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
    *,
    knowledge_artifact: KnowledgeStepArtifact,
) -> list[str]:
    items: list[str] = []
    severity = _code_severity(fault_codes, alarm_codes)
    if fault_codes or alarm_codes:
        items.extend(
            [
                "当前设备是否仍保持该事件码/告警码，以及是否已经执行过复位、参数恢复或运行模式切换。",
                "事件码出现前后的参数修改记录、单位设置变更记录和功能块激活时间点。",
                "复位或参数恢复前后的状态字、控制字、运行命令和事件码变化。",
            ]
        )
        if severity == "fault":
            items.append("F 类故障码是否触发停机、禁止合闸或驱动保护状态。")
        if not knowledge_artifact.success:
            items.append("知识库缺少该事件码/故障码的可靠释义、触发条件和处置步骤。")

    if rows:
        latest = rows[0]
        speed_deviation = _speed_deviation(latest)
        max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
        if speed_deviation is not None and speed_deviation >= 0.2:
            items.append("速度给定来源、运行使能、编码器或反馈链路是否与实际转速一致。")
        if max_load >= 75:
            items.append("机械传动、工艺负载、制动状态和限幅参数是否存在变化。")
    return _dedupe_items(items)


def _build_confidence_details(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
    *,
    knowledge_artifact: KnowledgeStepArtifact,
) -> list[str]:
    items: list[str] = []
    if fault_codes or alarm_codes:
        items.append("事件码识别：high，SQL 最近样本中存在有效事件码/告警码。")
        if knowledge_artifact.success:
            items.append("RAG 释义匹配：high，知识库已返回事件码/故障码原因或处理片段。")
        else:
            items.append("RAG 释义匹配：low，知识库未命中明确事件码/故障码条目。")
    else:
        items.append("事件码识别：medium，当前样本未见有效事件码/告警码，但仍需结合采样覆盖范围判断。")

    latest = rows[0] if rows else {}
    speed_deviation = _speed_deviation(latest) if latest else None
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
    if speed_deviation is not None and speed_deviation >= 0.2:
        items.append("速度偏差关联判断：medium，数据能证明偏差存在，但不能单独确认其根因。")
    if max_load >= 75:
        items.append("负载关联判断：medium，数据能证明负载进入关注区间，但需现场负载和机械检查闭环。")
    items.append("处置闭环：medium，需要现场复位、参数恢复或试运行结果确认。")
    return _dedupe_items(items)


def _build_risk_notice(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
) -> str:
    notices: list[str] = []
    severity = _code_severity(fault_codes, alarm_codes)
    if severity == "fault":
        notices.append("F 类故障码未闭环前，避免在原因和复位条件未确认的情况下反复复位、强启或继续带载试运行。")
    elif fault_codes or alarm_codes:
        notices.append("事件码未闭环前，避免反复改参或复位，以免掩盖参数、功能块和运行模式证据。")
    if rows:
        latest = rows[0]
        speed_deviation = _speed_deviation(latest)
        max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
        if speed_deviation is not None and speed_deviation >= 0.2:
            notices.append("速度给定与反馈偏差较大，试运行前应确认运行命令、反馈链路和负载状态。")
        if max_load >= 75:
            notices.append("负载率已进入关注区间，原因未确认前不宜扩大负载。")
    return " ".join(notices) or "当前未发现额外风险提示，仍需按现场安全规程执行复位和试运行。"


def build_analysis_evidence_summary(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
) -> str:
    """Build compact structured evidence for the LLM synthesis step."""

    sql_report = _build_sql_report_summary(sql_artifact, knowledge_artifact=knowledge_artifact)
    lines = [
        f"用户问题：{request.user_message}",
        f"分析目标：{request.analysis_goal}",
        f"SQL摘要：{sql_artifact.summary}",
        f"数据侧结论：{sql_report.summary}",
    ]
    if sql_report.rows:
        latest = sql_report.rows[0]
        fault_codes = _unique_codes(sql_report.rows, "fault_code")
        alarm_codes = _unique_codes(sql_report.rows, "alarm_code")
        code_label = _code_label(fault_codes, alarm_codes)
        signal_rows = _build_signal_rows(sql_report.rows, fault_codes, alarm_codes)
        lines.extend(
            [
                f"样本数：{len(sql_report.rows)}",
                f"最新时间：{_format_value(latest.get('create_time'))}",
                f"设备：{', '.join(_unique_non_empty(sql_report.rows, 'device_name')) or request.equipment_hint or '未识别'}",
                f"状态字：{_format_value(latest.get('status'))}",
                f"{code_label}：{', '.join(_effective_codes(fault_codes, alarm_codes)) if (fault_codes or alarm_codes) else '无'}",
                f"告警码：{', '.join(alarm_codes) if alarm_codes else '无'}",
                f"健康判定：{sql_report.health_level}",
                f"异常持续性：{signal_rows[0][1]}；{signal_rows[0][2]}",
            ]
        )
        for name, performance, hint in signal_rows[1:]:
            lines.append(f"{name}：{performance}；{hint}")
        knowledge_summaries = _knowledge_action_summaries(knowledge_artifact, fault_codes + alarm_codes)
        if knowledge_summaries:
            lines.append("RAG知识要点：")
            lines.extend(f"- {item}" for item in knowledge_summaries)
        elif knowledge_artifact.raw_output:
            lines.append(f"RAG检索状态：{knowledge_artifact.error or '已返回片段但未提取到明确处置步骤'}")
    elif knowledge_artifact.raw_output:
        lines.append(f"RAG知识片段：{knowledge_artifact.raw_output[:1200]}")
    return "\n".join(lines)


def _build_recommendation_items(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
    *,
    knowledge_artifact: KnowledgeStepArtifact,
) -> list[str]:
    items: list[str] = []
    code_summaries = _knowledge_action_summaries(knowledge_artifact, fault_codes + alarm_codes)
    if fault_codes or alarm_codes:
        severity = _code_severity(fault_codes, alarm_codes)
        if severity == "fault":
            items.append("立即确认：确认当前设备运行/停机状态、现场安全条件和故障保持状态；在复位条件未确认前避免反复复位、强启或继续带载试运行。")
        else:
            items.append(f"立即确认：确认 {REAL_DATA_LATEST_TABLE} 最新记录是否对应当前设备采样、DCMA 与设备映射关系，以及设备是否处于自动运行、调试、限速、点动或停机保持状态。")
        if code_summaries:
            for summary in code_summaries:
                label = "故障码处置" if severity == "fault" else "参数/配置检查"
                items.append(f"{label}：按 RAG 手册片段核对触发条件和处理项，操作前记录当前参数快照；{summary}")
        elif knowledge_artifact.success:
            items.append("已自动检索 RAG 知识片段，但片段中未抽取到明确处置步骤；先按数据侧异常特征复核复位条件、运行命令和关键电气量。")
        else:
            items.append("系统已自动检索知识库但未命中明确释义；当前建议先依据数据特征复核运行模式、参数状态和关键电气量，并将事件码释义作为后续知识库补齐项。")
        items.append("运行验证：记录复位、参数恢复或模式确认前后的状态字、控制字、母线电压、运行命令和事件码变化，确认事件是否消失或复现。")
    else:
        items.append("当前样本未见有效异常码，建议继续跟踪状态字、温度、负载率和功率指标。")

    latest = rows[0]
    speed_deviation = _speed_deviation(latest)
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
    max_motor_temp = _metric_max(rows, "motor_temp") or 0
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp") or 0
    if speed_deviation is not None and speed_deviation >= 0.2:
        items.append("关联排查：速度给定与反馈偏差较大，核对运行使能、速度给定来源、编码器/反馈链路和负载扰动。")
    if max_load >= 75:
        items.append("关联排查：负载率进入关注区间，检查机械传动、工艺负载、制动状态和参数限幅设置。")
    if max_motor_temp >= 60 or max_inverter_temp >= 50:
        items.append("关联排查：温度进入关注区间，检查风道、散热器、柜内温度和连续运行负载。")
    items.append("闭环确认：现场复核状态字、控制字、母线电压、温度、负载率和功率指标是否与设备现象一致。")
    return _dedupe_items(items)


def _build_sql_report_summary(
    sql_artifact: SqlStepArtifact,
    *,
    report_time: str | None = None,
    knowledge_artifact: KnowledgeStepArtifact | None = None,  # noqa: ARG001 - kept for caller compatibility
) -> SqlReportSummary:
    rows = _parse_sql_rows(sql_artifact.raw_output or sql_artifact.result_preview)
    if not rows:
        return SqlReportSummary(
            rows=[],
            summary=f"SQL 查询未返回可解析的 {REAL_DATA_LATEST_TABLE} 行数据。",
        )

    latest = rows[0]
    devices = _unique_non_empty(rows, "device_name")
    fault_codes = _unique_codes(rows, "fault_code")
    alarm_codes = _unique_codes(rows, "alarm_code")
    latest_time = _format_value(latest.get("create_time"))
    code_label = _code_label(fault_codes, alarm_codes)
    effective_codes = _effective_codes(fault_codes, alarm_codes)
    abnormal_text = f"发现{code_label}：{', '.join(effective_codes)}" if effective_codes else "未发现有效事件码/故障码"
    health_level = _derive_health_level(rows, fault_codes, alarm_codes)
    data_quality = _build_data_quality(rows, report_time=report_time)
    summary = (
        f"已从 {REAL_DATA_LATEST_TABLE} 获取 {len(rows)} 条 DCMA 运行数据，最新设备 {devices[0] if devices else '未知'} "
        f"在 {latest_time} 的状态为 {_format_value(latest.get('status'))}，综合判定：{health_level}；{abnormal_text}。"
    )
    return SqlReportSummary(
        rows=rows,
        summary=summary,
        chart_payload=_build_chart_payload(rows, data_quality=data_quality),
        health_level=health_level,
        data_quality=data_quality,
    )


def build_sql_report_summary(
    sql_artifact: SqlStepArtifact,
    *,
    report_time: str | None = None,
    knowledge_artifact: KnowledgeStepArtifact | None = None,
) -> SqlReportSummary:
    return _build_sql_report_summary(
        sql_artifact,
        report_time=report_time,
        knowledge_artifact=knowledge_artifact,
    )


def build_structured_analysis_artifact(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
) -> AnalysisStepArtifact | None:
    sql_report = _build_sql_report_summary(sql_artifact, knowledge_artifact=knowledge_artifact)
    if not sql_report.rows:
        return None

    latest = sql_report.rows[0]
    devices = _unique_non_empty(sql_report.rows, "device_name")
    fault_codes = _unique_codes(sql_report.rows, "fault_code")
    alarm_codes = _unique_codes(sql_report.rows, "alarm_code")
    code_label = _code_label(fault_codes, alarm_codes)
    code_text = ", ".join(_effective_codes(fault_codes, alarm_codes)) if (fault_codes or alarm_codes) else "未见有效事件码/故障码"
    alarm_text = ", ".join(alarm_codes) if alarm_codes else "未见有效告警码"
    device_text = ", ".join(devices) or request.equipment_hint or "DCMA 系统"
    latest_time = _format_value(latest.get("create_time"))
    status = _format_value(latest.get("status"))
    conclusion = (
        f"DCMA 最近运行数据已从 {REAL_DATA_LATEST_TABLE} 获取，{device_text} 最新记录状态为 {status}，"
        f"综合判定：{sql_report.health_level}；{code_label}为 {code_text}，告警码为 {alarm_text}。"
    )
    if knowledge_artifact.success:
        conclusion += " 已自动补充知识库检索结果用于诊断说明。"
    elif fault_codes:
        conclusion += " 已自动查询知识库，但当前知识库未命中该事件码/故障码的明确释义。"

    basis = [
        f"SQL 返回 {len(sql_report.rows)} 条 {REAL_DATA_LATEST_TABLE} 最近运行记录。",
        f"最新记录时间 {latest_time}，设备 {device_text}，状态 {status}。",
        f"{code_label}统计：{code_text}；告警码统计：{alarm_text}。",
        f"运行健康判定：{sql_report.health_level}。",
        (
            "知识库已返回事件码/故障码相关片段。"
            if knowledge_artifact.success
            else "知识库未返回该事件码/故障码的明确片段。"
        ),
    ]
    knowledge_summaries = _knowledge_action_summaries(knowledge_artifact, fault_codes + alarm_codes)
    basis.extend(f"RAG 处置要点：{item}" for item in knowledge_summaries[:3])
    recommendations = _build_recommendation_items(
        sql_report.rows,
        fault_codes,
        alarm_codes,
        knowledge_artifact=knowledge_artifact,
    )
    probable_causes = _build_probable_cause_items(
        sql_report.rows,
        fault_codes,
        alarm_codes,
        knowledge_artifact=knowledge_artifact,
    )
    verification_items = _build_verification_items(
        sql_report.rows,
        fault_codes,
        alarm_codes,
        knowledge_artifact=knowledge_artifact,
    )
    confidence_details = _build_confidence_details(
        sql_report.rows,
        fault_codes,
        alarm_codes,
        knowledge_artifact=knowledge_artifact,
    )
    missing_information = []
    if (fault_codes or alarm_codes) and not knowledge_artifact.success:
        missing_information.append("知识库未命中异常码释义")
    missing_information.extend(["现场现象、复位结果、运行命令来源和参数变更记录"])

    return AnalysisStepArtifact(
        success=True,
        conclusion=conclusion,
        basis=basis,
        probable_causes=probable_causes,
        verification_items=verification_items,
        recommendations=recommendations,
        risk_notice=_build_risk_notice(sql_report.rows, fault_codes, alarm_codes),
        missing_information=missing_information,
        confidence_details=confidence_details,
        confidence="high" if knowledge_artifact.success or not (fault_codes or alarm_codes) else "medium",
    )


def build_workorder_suggestion(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
) -> WorkOrderSuggestion:
    """Compatibility wrapper for the work-order suggestion module."""

    from ..workorder_suggestions import build_workorder_suggestion as _build_workorder_suggestion

    return _build_workorder_suggestion(
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
    )


def extract_report_url(save_result: str) -> str | None:
    matched = _REPORT_URL_RE.search(save_result or "")
    return matched.group(1) if matched else None


def extract_report_filename(save_result: str, fallback: str | None = None) -> str | None:
    report_url = extract_report_url(save_result)
    if report_url:
        return report_url.split("/")[-1]
    return fallback


def build_report_payload(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    current_time: str,
    report_filename: str,
    workorder_suggestion: WorkOrderSuggestion | None = None,
) -> dict[str, Any]:
    title, diagnosis_type = _report_title_and_type(request)
    sql_report = _build_sql_report_summary(
        sql_artifact,
        report_time=current_time,
        knowledge_artifact=knowledge_artifact,
    )
    sql_statement_text = ";\n".join(sql_artifact.sql_used) or "无"
    operation_report = build_operation_diagnosis_report(
        request=request,
        title=title,
        report_time=current_time,
        diagnosis_type=diagnosis_type,
        rows=sql_report.rows,
        data_quality=sql_report.data_quality or {},
        status_summary=_build_status_summary(sql_report.rows, sql_report.data_quality),
        sql_summary=sql_artifact.summary or "无",
        sql_statement=sql_statement_text,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
    )

    return {
        "title": title,
        "report_filename": report_filename,
        "chart_payload": sql_report.chart_payload,
        "operation_report_payload": json.dumps(operation_report.model_dump(mode="json", exclude_none=True), ensure_ascii=False),
    }
