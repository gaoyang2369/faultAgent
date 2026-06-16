"""Report payload and final-answer formatting helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from .report_sections import (
    build_capability_boundary_markdown,
    build_sop_recommendations_markdown,
    build_workorder_todo_markdown,
    details_block,
)
from .reporting_defs import (
    CONTROL_WORD_BITS as _CONTROL_WORD_BITS,
    DATA_FRESHNESS_DELAYED_SECONDS as _DATA_FRESHNESS_DELAYED_SECONDS,
    DATA_FRESHNESS_FRESH_SECONDS as _DATA_FRESHNESS_FRESH_SECONDS,
    DC_VOLTAGE_LOWER as _DC_VOLTAGE_LOWER,
    DC_VOLTAGE_UPPER as _DC_VOLTAGE_UPPER,
    INVERTER_TEMP_CRITICAL as _INVERTER_TEMP_CRITICAL,
    INVERTER_TEMP_WARNING as _INVERTER_TEMP_WARNING,
    LOAD_CRITICAL as _LOAD_CRITICAL,
    LOAD_WARNING as _LOAD_WARNING,
    MOTOR_TEMP_CRITICAL as _MOTOR_TEMP_CRITICAL,
    MOTOR_TEMP_WARNING as _MOTOR_TEMP_WARNING,
    PRIMARY_LATEST_METRIC_KEYS as _PRIMARY_LATEST_METRIC_KEYS,
    SPEED_ERROR_CRITICAL_PERCENT as _SPEED_ERROR_CRITICAL_PERCENT,
    SPEED_ERROR_WARNING_PERCENT as _SPEED_ERROR_WARNING_PERCENT,
    STATUS_WORD_BITS as _STATUS_WORD_BITS,
    TREND_METRICS as _TREND_METRICS,
    TREND_METRIC_DEFS as _TREND_METRIC_DEFS,
    SqlReportSummary,
)
from .sql_result_parser import parse_sql_rows
from .sql_safety import REAL_DATA_LATEST_TABLE

_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9._\-]+\.(?:md|html))", re.IGNORECASE)
_EMPTY_CODE_VALUES = {"", "0", "0.0", "none", "null", "无", "正常", "nan"}
_SPARKLINE_BLOCKS = "▁▂▃▄▅▆▇█"
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



def _normalize_code(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in _EMPTY_CODE_VALUES else text


def _format_value(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"


def _format_float(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return _format_value(value)


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _is_fault_code(code: str) -> bool:
    return str(code or "").strip().upper().startswith("F")


def _is_alarm_event_code(code: str) -> bool:
    return str(code or "").strip().upper().startswith("A")


def _effective_codes(fault_codes: list[str], alarm_codes: list[str]) -> list[str]:
    return _dedupe_items([*fault_codes, *alarm_codes])


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


def _data_quality_markdown(data_quality: dict[str, object]) -> str:
    return _markdown_table(
        ["项目", "结果", "说明"],
        [
            [
                "样本窗口",
                f"{data_quality.get('oldest_sample_time', '-')} 至 {data_quality.get('latest_sample_time', '-')}",
                "基于运行数据 create_time 排序后的采样范围",
            ],
            ["样本量", f"{data_quality.get('sample_count', 0)} 条", "用于本次统计、趋势和异常持续性判断"],
            [
                "采样间隔",
                (
                    f"平均 {_format_duration(data_quality.get('average_sample_interval_seconds'))}，"
                    f"最大 {_format_duration(data_quality.get('max_sample_gap_seconds'))}"
                ),
                "用于识别采样缺口和趋势连续性",
            ],
            [
                "数据延迟",
                _format_duration(data_quality.get("freshness_seconds")),
                str(data_quality.get("currentness") or "缺少报告时间，未评估实时性"),
            ],
            [
                "指标完整率",
                str(data_quality.get("metric_availability") or "未评估"),
                f"缺失 {data_quality.get('missing_metric_value_count', 0)}/{data_quality.get('metric_value_count', 0)} 个趋势指标值",
            ],
            ["当前性判定", str(data_quality.get("freshness_label") or "未知"), str(data_quality.get("currentness") or "-")],
        ],
    )


def _parse_sql_rows(raw_output: str) -> list[dict[str, object]]:
    return parse_sql_rows(raw_output)


def _unique_non_empty(rows: list[dict[str, object]], key: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        value = _format_value(row.get(key))
        if value != "-" and value not in values:
            values.append(value)
    return values


def _unique_codes(rows: list[dict[str, object]], key: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        code = _normalize_code(row.get(key))
        if code and code not in values:
            values.append(code)
    return values


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "暂无可展示数据。"
    def table_cell(value: object) -> str:
        return _format_value(value).replace("\r\n", " ").replace("\n", " ").replace("|", r"\|")

    header_line = "| " + " | ".join(table_cell(header) for header in headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in headers) + " |"
    row_lines = ["| " + " | ".join(table_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header_line, sep_line, *row_lines])


def _sparkline(values: list[float], *, limit: int = 24) -> str:
    if not values:
        return "-"
    sampled = values[-limit:]
    low = min(sampled)
    high = max(sampled)
    if high == low:
        return _SPARKLINE_BLOCKS[len(_SPARKLINE_BLOCKS) // 2] * len(sampled)
    chars = []
    for value in sampled:
        index = round((value - low) / (high - low) * (len(_SPARKLINE_BLOCKS) - 1))
        chars.append(_SPARKLINE_BLOCKS[index])
    return "".join(chars)


def _metric_trend_rows(rows: list[dict[str, object]]) -> list[list[str]]:
    trend_rows: list[list[str]] = []
    chronological_rows = list(reversed(rows))
    for key, label in _TREND_METRICS:
        values = [value for row in chronological_rows if (value := _to_float(row.get(key))) is not None]
        if not values:
            continue
        latest = values[-1]
        average = sum(values) / len(values)
        trend_rows.append(
            [
                label,
                _format_float(latest),
                _format_float(min(values)),
                _format_float(max(values)),
                _format_float(average),
                _sparkline(values),
            ]
        )
    return trend_rows


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


def _count_abnormal_code_rows(rows: list[dict[str, object]]) -> list[list[str]]:
    return [[str(item["name"]), str(item["value"])] for item in _count_abnormal_codes(rows)]


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0%"
    value = numerator / denominator * 100
    return f"{f'{value:.1f}'.rstrip('0').rstrip('.')}%"


def _row_time(row: dict[str, object]) -> str:
    return _format_value(row.get("create_time") or row.get("timestamp"))


def _is_abnormal_row(row: dict[str, object]) -> bool:
    return bool(_normalize_code(row.get("fault_code")) or _normalize_code(row.get("alarm_code")))


def _latest_abnormal_streak(rows: list[dict[str, object]]) -> int:
    streak = 0
    for row in rows:
        if not _is_abnormal_row(row):
            break
        streak += 1
    return streak


def _metric_values(rows: list[dict[str, object]], key: str) -> list[float]:
    chronological_rows = list(reversed(rows))
    return [value for row in chronological_rows if (value := _to_float(row.get(key))) is not None]


def _metric_max(rows: list[dict[str, object]], *keys: str) -> float | None:
    values = [value for key in keys for value in _metric_values(rows, key)]
    return max(values) if values else None


def _speed_deviation(latest: dict[str, object]) -> float | None:
    setpoint = _to_float(latest.get("speed_setpoint"))
    actual = _to_float(latest.get("speed_actual"))
    if setpoint is None or actual is None or abs(setpoint) < 1:
        return None
    return abs(actual - setpoint) / max(abs(setpoint), 1)


def _speed_deviation_percent(latest: dict[str, object]) -> float | None:
    deviation = _speed_deviation(latest)
    return round(deviation * 100, 2) if deviation is not None else None


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


def _executive_summary_markdown(
    status_summary: dict[str, object],
    conclusion: str,
    data_quality: dict[str, object] | None,
) -> str:
    rows = [
        ["状态等级", str(status_summary.get("status_level") or "未知")],
        ["当前事件", str(status_summary.get("current_event") or "无")],
        ["关键现象", str(status_summary.get("key_phenomenon") or "-")],
        ["处置优先级", str(status_summary.get("priority") or "未知")],
        ["下一步动作", str(status_summary.get("next_action") or "-")],
    ]
    freshness_note = ""
    if data_quality:
        freshness_label = str(data_quality.get("freshness_label") or "")
        if freshness_label and freshness_label != "实时性良好":
            freshness_note = (
                "\n\n"
                f"数据时效提示：最新样本距报告时间约 {_format_duration(data_quality.get('freshness_seconds'))}，"
                f"{data_quality.get('currentness')}。"
            )
    source_line = (
        f"数据来源：{status_summary.get('source_table') or REAL_DATA_LATEST_TABLE}；"
        f"样本窗口：{status_summary.get('sample_window') or '-'}；"
        f"设备映射：{status_summary.get('device_mapping') or status_summary.get('device') or '未识别'}。"
    )
    sample_count = data_quality.get("sample_count") if data_quality else None
    source_intro = (
        f"已从 {status_summary.get('source_table') or REAL_DATA_LATEST_TABLE} 获取 {sample_count} 条 DCMA 运行数据。\n\n"
        if sample_count is not None
        else ""
    )
    return (
        f"{_markdown_table(['项目', '结果'], rows)}\n\n"
        f"一句话结论：{conclusion or status_summary.get('initial_assessment') or '-'}\n\n"
        f"{source_intro}{source_line}{freshness_note}"
    )


def _judgement_from_high_threshold(value: float | None, warning: float, critical: float, unit: str = "") -> str:
    if value is None:
        return "缺少数据，无法判定"
    value_text = f"{_format_float(value)}{unit}"
    if value >= critical:
        return f"{value_text}，达到高风险阈值"
    if value >= warning:
        return f"{value_text}，进入关注阈值"
    return f"{value_text}，未超过关注阈值"


def _dc_voltage_judgement(value: float | None) -> str:
    if value is None:
        return "缺少数据，无法判定"
    value_text = f"{_format_float(value)} V"
    if value < _DC_VOLTAGE_LOWER or value > _DC_VOLTAGE_UPPER:
        return f"{value_text}，超出默认参考范围"
    return f"{value_text}，处于默认参考范围"


def _build_engineering_metric_rows(rows: list[dict[str, object]]) -> list[list[str]]:
    if not rows:
        return []
    latest = rows[0]
    speed_setpoint = _to_float(latest.get("speed_setpoint"))
    speed_actual = _to_float(latest.get("speed_actual"))
    speed_error = _speed_deviation_percent(latest)
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate")
    motor_temp = _to_float(latest.get("motor_temp"))
    inverter_temp = _to_float(latest.get("inverter_temp"))
    dc_voltage = _to_float(latest.get("dc_voltage"))
    current_actual = _to_float(latest.get("current_actual"))
    actual_power = _to_float(latest.get("actual_power"))
    speed_current = (
        f"给定 {_format_float(speed_setpoint)} rpm，实际 {_format_float(speed_actual)} rpm，偏差 "
        f"{_format_float(speed_error)}%"
        if speed_error is not None
        else f"给定 {_format_float(speed_setpoint)} rpm，实际 {_format_float(speed_actual)} rpm"
    )
    return [
        [
            "速度跟随偏差",
            speed_current,
            "|给定-实际| / max(|给定|, 1)",
            f"关注 ≥ {_format_float(_SPEED_ERROR_WARNING_PERCENT)}%，高风险 ≥ {_format_float(_SPEED_ERROR_CRITICAL_PERCENT)}%",
            _judgement_from_high_threshold(speed_error, _SPEED_ERROR_WARNING_PERCENT, _SPEED_ERROR_CRITICAL_PERCENT, "%"),
        ],
        [
            "母线电压",
            f"{_format_float(dc_voltage)} V",
            "最新 dc_voltage",
            f"默认参考 {_format_float(_DC_VOLTAGE_LOWER)}-{_format_float(_DC_VOLTAGE_UPPER)} V，可按现场标定调整",
            _dc_voltage_judgement(dc_voltage),
        ],
        [
            "电机温度",
            f"{_format_float(motor_temp)} ℃",
            "最新 motor_temp",
            f"关注 ≥ {_format_float(_MOTOR_TEMP_WARNING)} ℃，高风险 ≥ {_format_float(_MOTOR_TEMP_CRITICAL)} ℃",
            _judgement_from_high_threshold(motor_temp, _MOTOR_TEMP_WARNING, _MOTOR_TEMP_CRITICAL, " ℃"),
        ],
        [
            "变频器温度",
            f"{_format_float(inverter_temp)} ℃",
            "最新 inverter_temp",
            f"关注 ≥ {_format_float(_INVERTER_TEMP_WARNING)} ℃，高风险 ≥ {_format_float(_INVERTER_TEMP_CRITICAL)} ℃",
            _judgement_from_high_threshold(inverter_temp, _INVERTER_TEMP_WARNING, _INVERTER_TEMP_CRITICAL, " ℃"),
        ],
        [
            "负载率",
            f"样本最高 {_format_float(max_load)}%",
            "max(inverter_load_rate, motor_load_rate)",
            f"关注 ≥ {_format_float(_LOAD_WARNING)}%，高风险 ≥ {_format_float(_LOAD_CRITICAL)}%",
            _judgement_from_high_threshold(max_load, _LOAD_WARNING, _LOAD_CRITICAL, "%"),
        ],
        [
            "电流/功率",
            f"电流 {_format_float(current_actual)} A，功率 {_format_float(actual_power)} kW",
            "最新 current_actual / actual_power",
            "当前系统未配置该指标工程阈值",
            "仅展示当前值和趋势，不作异常判定",
        ],
    ]


def _engineering_metric_markdown(rows: list[dict[str, object]]) -> str:
    return _markdown_table(
        ["指标", "当前值", "计算/取值方式", "阈值依据", "判定"],
        _build_engineering_metric_rows(rows),
    )


def _parse_word_value(value: object) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _format_word_binary(value: object) -> str:
    parsed = _parse_word_value(value)
    if parsed is None:
        return "无法解析"
    bits = f"{parsed & 0xFFFF:016b}"
    return " ".join(bits[index : index + 4] for index in range(0, 16, 4))


def _word_bit_summary(value: object, definitions: tuple[tuple[int, str], ...]) -> str:
    parsed = _parse_word_value(value)
    if parsed is None:
        return "原始值不可解析，暂无法做 bit 级解释"
    enabled = [label for bit, label in definitions if parsed & (1 << bit)]
    disabled = [label for bit, label in definitions if not parsed & (1 << bit)]
    enabled_text = "、".join(enabled[:8]) if enabled else "无已知位被置位"
    disabled_text = "、".join(disabled[:4]) if disabled else "无"
    return f"已置位：{enabled_text}；未置位参考：{disabled_text}"


def _word_parse_markdown(latest: dict[str, object]) -> str:
    rows = [
        [
            "控制字 control_word",
            _format_value(latest.get("control_word")),
            _format_word_binary(latest.get("control_word")),
            _word_bit_summary(latest.get("control_word"), _CONTROL_WORD_BITS),
        ],
        [
            "状态字 status_word",
            _format_value(latest.get("status_word")),
            _format_word_binary(latest.get("status_word")),
            _word_bit_summary(latest.get("status_word"), _STATUS_WORD_BITS),
        ],
    ]
    table = _markdown_table(["对象", "原始值", "16 位二进制", "通用位解析"], rows)
    return (
        f"{table}\n\n"
        "> 说明：控制字/状态字解析采用通用 Siemens/SINAMICS STW1/ZSW1 位定义作为参考，"
        "现场项目如有自定义报文或版本差异，应以设备参数手册和 PLC 映射表为准。"
    )


def _latest_code_streak(rows: list[dict[str, object]], code: str) -> int:
    target = code.strip()
    streak = 0
    for row in rows:
        row_codes = {_normalize_code(row.get("fault_code")), _normalize_code(row.get("alarm_code"))}
        if target in row_codes:
            streak += 1
        else:
            break
    return streak


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


def _event_timeline_markdown(rows: list[dict[str, object]]) -> str:
    timeline_items = _event_timeline_items(rows)
    if not timeline_items:
        return "样本窗口内未见有效事件码或告警码。"
    table_rows = [
        [
            str(item["code"]),
            f"{item['count']}/{item['sample_count']}",
            str(item["ratio"]),
            f"{item['latest_streak']} 条",
            str(item["continuity"]),
        ]
        for item in timeline_items
    ]
    return _markdown_table(["事件码/告警码", "出现次数", "占比", "最新连续", "持续性"], table_rows)


def _layered_judgement_markdown(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
    *,
    knowledge_artifact: KnowledgeStepArtifact,
) -> str:
    if not rows:
        return "#### 数据事实\n- 【数据事实】SQL 未返回可解析运行数据。\n\n#### Agent 推断\n- 【Agent 推断】当前无法形成可靠运行状态判断。"
    latest = rows[0]
    abnormal_count = sum(1 for row in rows if _is_abnormal_row(row))
    codes = _effective_codes(fault_codes, alarm_codes)
    knowledge_summaries = _knowledge_action_summaries(knowledge_artifact, codes, per_code_limit=3)
    alarm_code_values = _unique_codes(rows, "alarm_code")
    speed_error = _speed_deviation_percent(latest)
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate")
    max_motor_temp = _metric_max(rows, "motor_temp")
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp")

    facts = [
        f"- 【数据事实】样本窗口包含 {len(rows)} 条 {REAL_DATA_LATEST_TABLE} 记录，{abnormal_count} 条含有效事件码/告警码。",
        f"- 【数据事实】最新记录时间 {_format_value(latest.get('create_time'))}，设备 {_format_value(latest.get('device_name'))}，状态 {_format_value(latest.get('status'))}。",
    ]
    if codes:
        facts.append(f"- 【数据事实】当前事件码/告警码：{', '.join(codes)}。")
    if not alarm_code_values:
        facts.append("- 【数据事实】alarm_code 字段未见有效告警值。")
    facts.append(f"- 【数据事实】关键运行现象：{_key_phenomenon(rows)}。")
    if knowledge_summaries:
        knowledge_lines = [f"- 【知识库解释】{item}" for item in knowledge_summaries]
    elif codes:
        knowledge_lines = ["- 【知识库解释】知识库未命中足够明确的事件码释义，不能补充厂家手册结论。"]
    else:
        knowledge_lines = ["- 【知识库解释】当前样本未检出有效事件码，未触发故障码释义依赖。"]

    if any(_is_alarm_event_code(code) for code in codes) and not any(_is_fault_code(code) for code in codes):
        knowledge_lines.append("- 【知识库解释】A 类代码按事件/告警/参数配置线索处理，不能直接等同于机械故障。")

    timeline_items = _event_timeline_items(rows)
    if timeline_items:
        event_summary = "；".join(
            f"{item['code']} 出现 {item['count']}/{item['sample_count']}，最新连续 {item['latest_streak']} 条，{item['continuity']}"
            for item in timeline_items[:3]
        )
    else:
        event_summary = "样本窗口内未见有效事件码或告警码"
    rule_lines = [
        f"- 【规则判断】事件持续性：{event_summary}。",
    ]
    if speed_error is not None:
        rule_lines.append(
            f"- 【规则判断】速度偏差率 {speed_error:g}%，"
            f"关注阈值 {_SPEED_ERROR_WARNING_PERCENT:g}%，高风险阈值 {_SPEED_ERROR_CRITICAL_PERCENT:g}%。"
        )
    if max_load is not None:
        rule_lines.append(
            f"- 【规则判断】样本最高负载率 {_format_float(max_load)}%，"
            f"关注阈值 {_LOAD_WARNING:g}%，高风险阈值 {_LOAD_CRITICAL:g}%。"
        )
    if max_motor_temp is not None or max_inverter_temp is not None:
        rule_lines.append(
            "- 【规则判断】"
            f"电机最高温度 {_format_float(max_motor_temp)}℃，"
            f"变频器最高温度 {_format_float(max_inverter_temp)}℃。"
        )
    inference = [
        f"- 【Agent 推断】{_initial_assessment(rows, fault_codes, alarm_codes)}",
        "- 【Agent 推断】当前不能仅凭事件码证明速度偏差、负载变化或温度变化由该事件直接导致，仍需结合控制模式、状态字、控制字、限幅状态和现场工况确认。",
        f"- 【Agent 推断】建议下一步：{_next_action(rows, fault_codes, alarm_codes)}",
    ]
    return "\n\n".join(
        [
            "#### 数据事实",
            "\n".join(facts),
            "#### 知识库解释",
            "\n".join(knowledge_lines),
            "#### 规则判断",
            "\n".join(rule_lines),
            "#### Agent 推断",
            "\n".join(inference),
        ]
    )


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


def _dedupe_items(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


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
    knowledge_artifact: KnowledgeStepArtifact | None = None,
) -> SqlReportSummary:
    rows = _parse_sql_rows(sql_artifact.raw_output or sql_artifact.result_preview)
    if not rows:
        return SqlReportSummary(
            rows=[],
            summary=f"SQL 查询未返回可解析的 {REAL_DATA_LATEST_TABLE} 行数据。",
            details_markdown=f"【SQL 结果摘要】\n{sql_artifact.result_preview or sql_artifact.raw_output or '无'}",
            fault_inference="当前报告无法基于数据库行数据确认运行状态，请先核对 SQL 查询条件和数据库连接。",
            maintenance=f"建议先确认 {REAL_DATA_LATEST_TABLE} 最近数据是否可查询，再补充设备范围或时间范围后重新生成报告。",
        )

    latest = rows[0]
    devices = _unique_non_empty(rows, "device_name")
    fault_codes = _unique_codes(rows, "fault_code")
    alarm_codes = _unique_codes(rows, "alarm_code")
    latest_time = _format_value(latest.get("create_time"))
    status = _format_value(latest.get("status"))
    code_label = _code_label(fault_codes, alarm_codes)
    effective_codes = _effective_codes(fault_codes, alarm_codes)
    abnormal_text = f"发现{code_label}：{', '.join(effective_codes)}" if effective_codes else "未发现有效事件码/故障码"
    abnormal_count = sum(1 for row in rows if _is_abnormal_row(row))
    latest_streak = _latest_abnormal_streak(rows)
    oldest_time = _row_time(rows[-1])
    health_level = _derive_health_level(rows, fault_codes, alarm_codes)
    data_quality = _build_data_quality(rows, report_time=report_time)
    status_summary = _build_status_summary(rows, data_quality)

    latest_rows = rows[:5]
    state_table = _markdown_table(
        ["时间", "设备", "状态", "故障/事件码", "告警码", "母线电压(V)", "实际转速", "实际电流", "电机温度", "变频器温度"],
        [
            [
                _format_value(row.get("create_time")),
                _format_value(row.get("device_name")),
                _format_value(row.get("status")),
                _normalize_code(row.get("fault_code")) or "无",
                _normalize_code(row.get("alarm_code")) or "无",
                _format_float(row.get("dc_voltage")),
                _format_float(row.get("speed_actual")),
                _format_float(row.get("current_actual")),
                _format_float(row.get("motor_temp")),
                _format_float(row.get("inverter_temp")),
            ]
            for row in latest_rows
        ],
    )
    metric_table = _markdown_table(
        ["指标", "最新值"],
        [
            ["给定转速", _format_float(latest.get("speed_setpoint"))],
            ["实际转速", _format_float(latest.get("speed_actual"))],
            ["给定转矩", _format_float(latest.get("torque_setpoint"))],
            ["实际转矩", _format_float(latest.get("torque_actual"))],
            ["实际功率", _format_float(latest.get("actual_power"))],
            ["励磁电流", _format_float(latest.get("field_current"))],
            ["转矩电流", _format_float(latest.get("torque_current"))],
            ["变频器负载率", _format_float(latest.get("inverter_load_rate"))],
            ["电机负载率", _format_float(latest.get("motor_load_rate"))],
            ["反馈功率", _format_float(latest.get("feedback_power"))],
        ],
    )
    abnormal_rows = [
        [
            _format_value(row.get("create_time")),
            _format_value(row.get("device_name")),
            _format_value(row.get("status")),
            _normalize_code(row.get("fault_code")) or "无",
            _normalize_code(row.get("alarm_code")) or "无",
        ]
        for row in rows
        if _normalize_code(row.get("fault_code")) or _normalize_code(row.get("alarm_code"))
    ][:10]
    abnormal_table = _markdown_table(["时间", "设备", "状态", "故障/事件码", "告警码"], abnormal_rows)
    trend_table = _markdown_table(
        ["指标", "最新", "最小", "最大", "平均", "趋势"],
        _metric_trend_rows(rows),
    )
    status_table = _markdown_table(["状态字", "记录数"], _count_rows(rows, "status"))
    fault_count_table = _markdown_table(["异常码/告警码", "记录数"], _count_abnormal_code_rows(rows))
    health_table = _markdown_table(
        ["维度", "结果"],
        [
            ["数据来源", f"{REAL_DATA_LATEST_TABLE}（最新运行数据分表）"],
            ["健康判定", health_level],
            ["采样窗口", f"{oldest_time} 至 {latest_time}"],
            ["异常占比", f"{abnormal_count}/{len(rows)}（{_format_percent(abnormal_count, len(rows))}）"],
            ["最新连续异常", f"{latest_streak} 条"],
            ["最新控制字 / 状态字", f"{_format_value(latest.get('control_word'))} / {_format_value(latest.get('status_word'))}"],
        ],
    )
    signal_table = _markdown_table(
        ["观察项", "数据表现", "诊断提示"],
        _build_signal_rows(rows, fault_codes, alarm_codes),
    )
    layered_knowledge_artifact = knowledge_artifact or KnowledgeStepArtifact(success=False, query="", raw_output="")
    overview_table = _markdown_table(
        ["维度", "结果"],
        [
            ["数据样本", f"{len(rows)} 条"],
            ["覆盖设备", ", ".join(devices) or "未识别"],
            ["最新记录时间", latest_time],
            ["最新状态字", status],
            ["故障/事件码", ", ".join(fault_codes) if fault_codes else "无"],
            ["告警码", ", ".join(alarm_codes) if alarm_codes else "无"],
        ],
    )

    health_overview_details = f"{health_table}\n\n{overview_table}\n\n{signal_table}"
    latest_sample_details = f"{state_table}\n\n{metric_table}"
    distribution_details = f"{status_table}\n\n{fault_count_table}\n\n{abnormal_table}"
    details = (
        f"### 诊断证据链\n{_layered_judgement_markdown(rows, fault_codes, alarm_codes, knowledge_artifact=layered_knowledge_artifact)}\n\n"
        f"### 事件码时间线\n{_event_timeline_markdown(rows)}\n\n"
        f"### 关键指标工程判定\n{_engineering_metric_markdown(rows)}\n\n"
        f"{details_block('展开查看：数据质量与实时性', _data_quality_markdown(data_quality))}\n\n"
        f"{details_block('展开查看：运行健康与采样概览', health_overview_details)}\n\n"
        f"{details_block('展开查看：控制字 / 状态字解析', _word_parse_markdown(latest))}\n\n"
        f"{details_block('展开查看：完整趋势统计', trend_table)}\n\n"
        f"{details_block('展开查看：最新采样明细', latest_sample_details)}\n\n"
        f"{details_block('展开查看：状态与异常分布', distribution_details)}"
    )
    summary = (
        f"已从 {REAL_DATA_LATEST_TABLE} 获取 {len(rows)} 条 DCMA 运行数据，最新设备 {devices[0] if devices else '未知'} "
        f"在 {latest_time} 的状态为 {status}，综合判定：{health_level}；{abnormal_text}。"
    )
    inference_items = [
        f"综合判定为“{health_level}”。",
        f"最近 {len(rows)} 条样本中 {abnormal_count} 条包含有效事件码/告警码，最新连续异常 {latest_streak} 条。",
        f"最新状态字为 {status}，控制字/状态字为 {_format_value(latest.get('control_word'))}/{_format_value(latest.get('status_word'))}。",
    ]
    if fault_codes or alarm_codes:
        inference_items.append(f"数据库最近记录中{abnormal_text}，需结合厂家手册确认代码含义、反应和复位条件。")
    else:
        inference_items.append("数据库最近记录未显示有效事件码/告警码，当前更偏向运行状态巡检结论。")
    inference_items.extend(row[1] for row in _build_signal_rows(rows, fault_codes, alarm_codes)[1:4])
    fault_inference = "\n".join(f"- {item}" for item in inference_items)
    maintenance = "\n".join(
        f"- {item}"
        for item in [
            f"将 {REAL_DATA_LATEST_TABLE} 的最新写入时间、写入频率和事件码占比纳入日常健康检查。",
            "为高频事件码/故障码建立手册释义、触发条件、复位条件和现场检查项的结构化知识条目。",
            "对速度偏差、负载率、温度、母线电压设置分级阈值，形成“关注/预警/停机检查”的处置规则。",
            "保留复位前后各 1-3 分钟运行快照，便于复盘异常是否由工艺负载、供电或控制命令触发。",
        ]
    )
    return SqlReportSummary(
        rows=rows,
        summary=summary,
        details_markdown=details,
        fault_inference=fault_inference,
        maintenance=maintenance,
        chart_payload=_build_chart_payload(rows, data_quality=data_quality),
        health_level=health_level,
        data_quality=data_quality,
    )


def _knowledge_report_section(knowledge_artifact: KnowledgeStepArtifact) -> str:
    raw_output = (knowledge_artifact.raw_output or "").strip()
    if not raw_output:
        return "知识库未返回事件码/故障码相关内容。"
    if not knowledge_artifact.success:
        return f"知识库检索结果：{raw_output}"
    return raw_output[:2000].strip()


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


def _workorder_priority_label(risk_level: str) -> str:
    normalized = str(risk_level or "").strip()
    if normalized == "高":
        return "高优先级"
    if normalized == "中":
        return "中优先级"
    if normalized == "低":
        return "低优先级"
    return "中优先级"


def _workorder_completion_window(risk_level: str) -> str:
    normalized = str(risk_level or "").strip()
    if normalized == "高":
        return "4小时内"
    if normalized == "中":
        return "24小时内"
    return "72小时内"


def _workorder_knowledge_hint(knowledge_artifact: KnowledgeStepArtifact, codes: list[str]) -> str:
    summaries = _knowledge_action_summaries(knowledge_artifact, codes, per_code_limit=2)
    for item in summaries:
        if "：" in item:
            label, value = item.split("：", 1)
            if label.strip() in {"原因", "含义", "说明"}:
                return value.strip().rstrip("。；;")
    return summaries[0].strip().rstrip("。；;") if summaries else ""


def _workorder_title(device_text: str, primary_code: str, workorder_type: str) -> str:
    code_part = primary_code or "运行异常"
    if workorder_type == "温升异常排查":
        return f"{device_text} 温升异常排查"
    if workorder_type == "供电检查":
        return f"{device_text} 供电检查"
    return f"{device_text} {code_part} 事件及速度偏差排查" if primary_code else f"{device_text} 运行异常排查"


def _workorder_steps(
    *,
    primary_code: str,
    speed_trigger: bool,
    load_trigger: bool,
    temp_trigger: bool,
    voltage_trigger: bool,
) -> list[str]:
    steps = ["备份当前参数快照"]
    if primary_code:
        steps.append("核查单位制相关参数")
        steps.append("按手册建议恢复单位设置")
        steps.append(f"重新激活功能块并观察 {primary_code} 是否复现")
    if speed_trigger:
        steps.append("复核速度设定与反馈链路")
        steps.append("检查编码器信号与速度反馈一致性")
    if load_trigger:
        steps.append("检查负载波动、机械阻滞和制动状态")
    if temp_trigger:
        steps.append("检查散热与柜内温度")
    if voltage_trigger:
        steps.append("检查供电与母线电压波动")
    return _dedupe_items(steps)


def _workorder_acceptance_criteria(
    *,
    primary_code: str,
    speed_trigger: bool,
    load_trigger: bool,
    temp_trigger: bool,
    voltage_trigger: bool,
) -> list[str]:
    criteria = []
    if primary_code:
        criteria.append(f"{primary_code} 不再持续出现")
    if speed_trigger:
        criteria.append("速度偏差恢复至阈值以内")
    if load_trigger:
        criteria.append("负载率回落至正常区间")
    if temp_trigger:
        criteria.append("温度回落到关注阈值以下")
    if voltage_trigger:
        criteria.append("母线电压波动恢复正常")
    if (primary_code or speed_trigger or load_trigger) and not temp_trigger and not voltage_trigger:
        criteria.append("温度和母线电压无新增异常")
    return _dedupe_items(criteria)


def _workorder_task_mappings(
    *,
    primary_code: str,
    primary_streak: int,
    speed_deviation: float | None,
    max_load: float | None,
    max_motor_temp: float | None,
    max_inverter_temp: float | None,
    voltage_min: float | None,
    voltage_max: float | None,
    speed_trigger: bool,
    load_trigger: bool,
    temp_trigger: bool,
    voltage_trigger: bool,
) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    if primary_code:
        evidence = f"{primary_code} 持续出现 {primary_streak} 条" if primary_streak > 1 else f"最近样本出现 {primary_code}"
        mappings.append(
            {
                "evidence": evidence,
                "tasks": [
                    "核查单位制相关参数",
                    "按手册建议恢复单位设置",
                    f"重新激活功能块并观察 {primary_code} 是否复现",
                ],
            }
        )
    if speed_trigger and speed_deviation is not None:
        mappings.append(
            {
                "evidence": f"速度偏差 {_format_float(speed_deviation)}%",
                "tasks": ["复核速度设定与反馈链路", "检查编码器信号与速度反馈一致性"],
            }
        )
    if load_trigger and max_load is not None:
        mappings.append(
            {
                "evidence": f"负载率 {_format_float(max_load)}%",
                "tasks": ["检查负载波动、机械阻滞和制动状态"],
            }
        )
    if not temp_trigger and (max_motor_temp is not None or max_inverter_temp is not None):
        mappings.append(
            {
                "evidence": f"温度正常，电机最高 {_format_float(max_motor_temp)}℃，变频器最高 {_format_float(max_inverter_temp)}℃",
                "tasks": ["暂不生成温升排查任务"],
            }
        )
    if voltage_min is not None and voltage_max is not None:
        if voltage_trigger:
            tasks = ["检查供电与母线电压波动"]
            evidence = f"母线电压 {_format_float(voltage_min)}-{_format_float(voltage_max)}V 波动异常"
        else:
            tasks = ["暂不生成供电异常排查任务"]
            evidence = f"母线电压 {_format_float(voltage_min)}-{_format_float(voltage_max)}V 基本稳定"
        mappings.append({"evidence": evidence, "tasks": tasks})
    return mappings[:6]


def build_workorder_suggestion(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
) -> WorkOrderSuggestion:
    sql_report = _build_sql_report_summary(sql_artifact, knowledge_artifact=knowledge_artifact)
    if not sql_report.rows:
        return WorkOrderSuggestion(
            need_workorder=False,
            reason="SQL 未返回可解析运行数据，暂不自动生成工单。",
            workorder_type="",
            priority="P2",
            priority_label=_workorder_priority_label("低"),
            risk_level="低",
            assignee_role="",
            suggested_completion_window="",
            diagnosis_conclusion=analysis_artifact.conclusion,
            key_evidence=[],
            processing_steps=[],
            acceptance_criteria=[],
            task_mappings=[],
            equipment_object=request.equipment_hint or "DCMA 系统",
            fault_code=None,
            title="",
            trigger_source="故障诊断 Agent",
            status="待派单",
        )

    latest = sql_report.rows[0]
    devices = _unique_non_empty(sql_report.rows, "device_name")
    fault_codes = _unique_codes(sql_report.rows, "fault_code")
    alarm_codes = _unique_codes(sql_report.rows, "alarm_code")
    effective_codes = _effective_codes(fault_codes, alarm_codes)
    primary_code = effective_codes[0] if effective_codes else ""
    primary_streak = _latest_code_streak(sql_report.rows, primary_code) if primary_code else 0
    speed_deviation = _speed_deviation_percent(latest)
    max_load = _metric_max(sql_report.rows, "inverter_load_rate", "motor_load_rate")
    max_motor_temp = _metric_max(sql_report.rows, "motor_temp")
    max_inverter_temp = _metric_max(sql_report.rows, "inverter_temp", "inverter_radiator_temp")
    dc_voltage_values = _metric_values(sql_report.rows, "dc_voltage")
    voltage_min = min(dc_voltage_values) if dc_voltage_values else None
    voltage_max = max(dc_voltage_values) if dc_voltage_values else None
    voltage_trigger = False
    if voltage_min is not None and (voltage_min < _DC_VOLTAGE_LOWER or (voltage_max is not None and voltage_max > _DC_VOLTAGE_UPPER)):
        voltage_trigger = True

    speed_trigger = speed_deviation is not None and speed_deviation >= _SPEED_ERROR_WARNING_PERCENT
    load_trigger = max_load is not None and max_load >= _LOAD_WARNING
    temp_trigger = (
        (max_motor_temp is not None and max_motor_temp >= _MOTOR_TEMP_WARNING)
        or (max_inverter_temp is not None and max_inverter_temp >= _INVERTER_TEMP_WARNING)
    )
    severe_trigger = bool(
        any(_is_fault_code(code) for code in effective_codes)
        or primary_streak >= 3
        or (speed_deviation is not None and speed_deviation >= _SPEED_ERROR_CRITICAL_PERCENT)
        or (max_load is not None and max_load >= _LOAD_CRITICAL)
        or (max_motor_temp is not None and max_motor_temp >= _MOTOR_TEMP_CRITICAL)
        or (max_inverter_temp is not None and max_inverter_temp >= _INVERTER_TEMP_CRITICAL)
        or voltage_trigger
    )
    need_workorder = bool(severe_trigger or speed_trigger or load_trigger or temp_trigger)

    risk_level = "高" if (
        any(_is_fault_code(code) for code in effective_codes)
        or (speed_deviation is not None and speed_deviation >= _SPEED_ERROR_CRITICAL_PERCENT)
        or (max_load is not None and max_load >= _LOAD_CRITICAL)
        or (max_motor_temp is not None and max_motor_temp >= _MOTOR_TEMP_CRITICAL)
        or (max_inverter_temp is not None and max_inverter_temp >= _INVERTER_TEMP_CRITICAL)
        or voltage_trigger
    ) else "中" if need_workorder else "低"

    if any(_is_fault_code(code) for code in effective_codes) or primary_streak >= 3 or speed_trigger or load_trigger:
        workorder_type = "参数复核 / 运行异常排查"
    elif temp_trigger:
        workorder_type = "温升异常排查"
    elif voltage_trigger:
        workorder_type = "供电检查"
    else:
        workorder_type = "运行异常排查"

    device_label = devices[0] if devices else (request.equipment_hint or "DCMA 系统")
    equipment_object = (
        device_label if str(device_label).strip().startswith("DCMA") else f"DCMA / {device_label}"
    )
    knowledge_hint = _workorder_knowledge_hint(knowledge_artifact, effective_codes)
    if primary_code:
        code_text = primary_code
    elif effective_codes:
        code_text = " / ".join(effective_codes[:2])
    else:
        code_text = "运行异常"

    diagnosis_clauses: list[str] = []
    if knowledge_hint:
        diagnosis_clauses.append(f"{code_text} 相关知识库提示：{knowledge_hint}")
    elif effective_codes:
        diagnosis_clauses.append(f"{code_text} 为持续异常事件线索")
    if speed_trigger and speed_deviation is not None:
        diagnosis_clauses.append(f"速度偏差 { _format_float(speed_deviation)}%")
    if load_trigger and max_load is not None:
        diagnosis_clauses.append(f"负载率 { _format_float(max_load)}%")
    if temp_trigger:
        diagnosis_clauses.append(
            f"温度关注：电机 {_format_float(max_motor_temp)}℃，变频器 {_format_float(max_inverter_temp)}℃"
        )
    if voltage_trigger and voltage_min is not None and voltage_max is not None:
        diagnosis_clauses.append(f"母线电压 {_format_float(voltage_min)}-{_format_float(voltage_max)}V 波动异常")

    diagnosis_conclusion = "；".join(diagnosis_clauses) if diagnosis_clauses else analysis_artifact.conclusion

    key_evidence: list[str] = []
    if primary_code:
        if primary_streak > 1:
            key_evidence.append(f"最近 {primary_streak} 条均出现 {primary_code}")
        else:
            key_evidence.append(f"最近样本出现 {primary_code}")
    elif effective_codes:
        key_evidence.append(f"最近样本出现 {', '.join(effective_codes[:2])}")
    if speed_trigger and speed_deviation is not None:
        key_evidence.append(f"速度偏差 { _format_float(speed_deviation)}%")
    if load_trigger and max_load is not None:
        key_evidence.append(f"负载率 { _format_float(max_load)}%")
    if not temp_trigger and (max_motor_temp is not None or max_inverter_temp is not None):
        key_evidence.append(
            f"温度正常，电机最高 {_format_float(max_motor_temp)}℃，变频器最高 {_format_float(max_inverter_temp)}℃"
        )
    if voltage_min is not None and voltage_max is not None and not voltage_trigger:
        key_evidence.append(f"母线电压 {_format_float(voltage_min)}-{_format_float(voltage_max)}V")
    if knowledge_hint:
        key_evidence.append(f"RAG 提示：{knowledge_hint}")

    processing_steps = _workorder_steps(
        primary_code=primary_code,
        speed_trigger=speed_trigger,
        load_trigger=load_trigger,
        temp_trigger=temp_trigger,
        voltage_trigger=voltage_trigger,
    )
    acceptance_criteria = _workorder_acceptance_criteria(
        primary_code=primary_code,
        speed_trigger=speed_trigger,
        load_trigger=load_trigger,
        temp_trigger=temp_trigger,
        voltage_trigger=voltage_trigger,
    )
    task_mappings = _workorder_task_mappings(
        primary_code=primary_code,
        primary_streak=primary_streak,
        speed_deviation=speed_deviation,
        max_load=max_load,
        max_motor_temp=max_motor_temp,
        max_inverter_temp=max_inverter_temp,
        voltage_min=voltage_min,
        voltage_max=voltage_max,
        speed_trigger=speed_trigger,
        load_trigger=load_trigger,
        temp_trigger=temp_trigger,
        voltage_trigger=voltage_trigger,
    )

    reason_parts: list[str] = []
    if primary_code:
        if primary_streak >= 3:
            reason_parts.append(f"{primary_code} 持续出现 {primary_streak} 条")
        else:
            reason_parts.append(f"{primary_code} 事件持续存在")
    if speed_trigger and speed_deviation is not None:
        reason_parts.append(f"速度偏差 {_format_float(speed_deviation)}% 超过关注阈值")
    if load_trigger and max_load is not None:
        reason_parts.append(f"负载率 {_format_float(max_load)}% 进入关注区间")
    if temp_trigger:
        reason_parts.append("温度进入关注区间")
    if voltage_trigger:
        reason_parts.append("母线电压波动异常")
    if not reason_parts:
        reason_parts.append("当前样本未达到自动建单条件")
    reason = "；".join(reason_parts)

    title = _workorder_title(equipment_object, code_text if primary_code else "", workorder_type)
    assignee_role = "电气维护人员"
    completion_window = _workorder_completion_window(risk_level)

    return WorkOrderSuggestion(
        need_workorder=need_workorder,
        reason=reason,
        workorder_type=workorder_type,
        priority="P1" if need_workorder else "P2",
        priority_label=_workorder_priority_label(risk_level if need_workorder else "低"),
        risk_level=risk_level,
        assignee_role=assignee_role,
        suggested_completion_window=completion_window,
        diagnosis_conclusion=diagnosis_conclusion,
        key_evidence=_dedupe_items(key_evidence)[:5],
        processing_steps=processing_steps[:8],
        acceptance_criteria=acceptance_criteria[:6],
        task_mappings=task_mappings,
        equipment_object=equipment_object,
        fault_code=primary_code or None,
        title=title,
        trigger_source="故障诊断 Agent",
        status="待派单",
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
    executive_summary = analysis_artifact.conclusion
    generated_recommendations: list[str] = []
    preventive_maintenance = sql_report.maintenance
    if sql_report.rows:
        status_summary = _build_status_summary(sql_report.rows, sql_report.data_quality)
        fault_codes = _unique_codes(sql_report.rows, "fault_code")
        alarm_codes = _unique_codes(sql_report.rows, "alarm_code")
        generated_recommendations = _build_recommendation_items(
            sql_report.rows,
            fault_codes,
            alarm_codes,
            knowledge_artifact=knowledge_artifact,
        )
        executive_summary = _executive_summary_markdown(
            status_summary,
            analysis_artifact.conclusion,
            sql_report.data_quality,
        )
        preventive_maintenance = (
            f"{sql_report.maintenance}\n\n"
            f"### 报告能力边界\n{build_capability_boundary_markdown(REAL_DATA_LATEST_TABLE)}"
        )
    base_recommendations = build_sop_recommendations_markdown(
        generated_recommendations,
        analysis_artifact.recommendations,
    )
    workorder_section = ""
    if workorder_suggestion and workorder_suggestion.need_workorder:
        workorder_section = build_workorder_todo_markdown(
            title=workorder_suggestion.title,
            workorder_type=workorder_suggestion.workorder_type,
            risk_level=workorder_suggestion.risk_level,
            priority=workorder_suggestion.priority,
            priority_label=workorder_suggestion.priority_label,
            assignee_role=workorder_suggestion.assignee_role,
            suggested_completion_window=workorder_suggestion.suggested_completion_window,
            key_evidence=workorder_suggestion.key_evidence,
            processing_steps=workorder_suggestion.processing_steps,
            acceptance_criteria=workorder_suggestion.acceptance_criteria,
        )
    workorder_suffix = f"\n\n{workorder_section}" if workorder_section else ""
    repair_recommendations = f"{base_recommendations}{workorder_suffix}"
    sql_statement_text = ";\n".join(sql_artifact.sql_used) or "无"

    return {
        "title": title,
        "report_time": current_time,
        "diagnosis_object": request.equipment_hint or "DCMA 系统",
        "diagnosis_type": diagnosis_type,
        "executive_summary": executive_summary,
        "diagnosis_overview": (
            f"本报告由限制型单 Agent 生成，当前运行状态定义为数据库 {REAL_DATA_LATEST_TABLE} 的最新采样窗口状态，"
            f"已按受控 SQL 查询最近运行数据，并结合可用知识检索结果、指标趋势和规则判定形成结论。"
            "若 DCMA 实际覆盖多个设备，本报告仅代表本次数据窗口覆盖的设备对象。"
        ),
        "diagnosis_details": (
            f"{sql_report.details_markdown}\n\n"
            f"{details_block('展开查看：知识库原文与长片段', _knowledge_report_section(knowledge_artifact))}"
        ),
        "fault_inference": f"{analysis_artifact.conclusion}\n\n{sql_report.fault_inference}",
        "repair_recommendations": repair_recommendations,
        "preventive_maintenance": preventive_maintenance,
        "diagnosis_basis": (
            "### SQL 摘要\n"
            f"- {sql_artifact.summary or '无'}\n"
            f"- SQL 返回：{len(sql_report.rows)} 条可解析 {REAL_DATA_LATEST_TABLE} 行数据\n\n"
            "### SQL 语句\n"
            f"```sql\n{sql_statement_text}\n```\n\n"
            "### 知识与分析依据\n"
            f"- 知识查询：{knowledge_artifact.query or '无'}\n"
            f"- 分析依据：{'; '.join(analysis_artifact.basis) or '无'}"
        ),
        "report_filename": report_filename,
        "chart_payload": sql_report.chart_payload,
        "workorder_suggestion": workorder_suggestion.model_dump(exclude_none=True) if workorder_suggestion else None,
    }
