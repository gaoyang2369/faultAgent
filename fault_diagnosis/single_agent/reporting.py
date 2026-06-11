"""Report payload and final-answer formatting helpers."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
)
from .sql_safety import REAL_DATA_FALLBACK_COLUMN_NAMES, REAL_DATA_LATEST_TABLE

_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9._\-]+\.(?:md|html))", re.IGNORECASE)
_EMPTY_CODE_VALUES = {"", "0", "0.0", "none", "null", "无", "正常", "nan"}
_SPARKLINE_BLOCKS = "▁▂▃▄▅▆▇█"
_TREND_METRICS = (
    ("dc_voltage", "母线电压(V)"),
    ("motor_temp", "电机温度"),
    ("inverter_temp", "变频器温度"),
    ("speed_actual", "实际转速"),
    ("current_actual", "实际电流"),
    ("inverter_load_rate", "变频器负载率"),
    ("motor_load_rate", "电机负载率"),
    ("motor_power", "电机功率"),
)
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


@dataclass
class SqlReportSummary:
    rows: list[dict[str, object]]
    summary: str
    details_markdown: str
    fault_inference: str
    maintenance: str
    chart_payload: str = ""
    health_level: str = "未知"


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


def _parse_sql_rows(raw_output: str) -> list[dict[str, object]]:
    text = (raw_output or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []

    rows: list[dict[str, object]] = []
    for item in parsed:
        if not isinstance(item, (list, tuple)):
            continue
        row = {
            column: item[index] if index < len(item) else None
            for index, column in enumerate(REAL_DATA_FALLBACK_COLUMN_NAMES)
        }
        rows.append(row)
    return rows


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
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in headers) + " |"
    row_lines = ["| " + " | ".join(_format_value(cell) for cell in row) + " |" for row in rows]
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
    if fault_codes or alarm_codes:
        return "异常：检测到有效异常码"
    speed_deviation = _speed_deviation(latest)
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") or 0
    max_motor_temp = _metric_max(rows, "motor_temp") or 0
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp") or 0
    if (speed_deviation is not None and speed_deviation >= 0.2) or max_load >= 75 or max_motor_temp >= 60 or max_inverter_temp >= 50:
        return "需关注：关键指标存在偏离或接近关注区间"
    return "未见显著异常：样本内未发现有效异常码"


def _build_chart_payload(rows: list[dict[str, object]]) -> str:
    chronological_rows = list(reversed(rows))
    timestamps = [_row_time(row) for row in chronological_rows]
    trend_metrics = []
    for key, label in _TREND_METRICS:
        values = []
        for row in chronological_rows:
            value = _to_float(row.get(key))
            values.append(round(value, 4) if value is not None else None)
        if any(value is not None for value in values):
            trend_metrics.append({"key": key, "name": label, "values": values})

    def count_payload(key: str, *, normalize_code: bool = False) -> list[dict[str, object]]:
        return [
            {"name": name, "value": int(count)}
            for name, count in _count_rows(rows, key, normalize_code=normalize_code)
        ]

    latest = rows[0]
    latest_metrics = [
        {"name": "实际转速", "value": _to_float(latest.get("speed_actual"))},
        {"name": "实际电流", "value": _to_float(latest.get("current_actual"))},
        {"name": "电机温度", "value": _to_float(latest.get("motor_temp"))},
        {"name": "变频器温度", "value": _to_float(latest.get("inverter_temp"))},
        {"name": "变频器负载率", "value": _to_float(latest.get("inverter_load_rate"))},
        {"name": "电机负载率", "value": _to_float(latest.get("motor_load_rate"))},
    ]
    payload = {
        "source_table": REAL_DATA_LATEST_TABLE,
        "timestamps": timestamps,
        "trend_metrics": trend_metrics,
        "status_counts": count_payload("status"),
        "fault_counts": count_payload("fault_code", normalize_code=True),
        "latest_metrics": [item for item in latest_metrics if item["value"] is not None],
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
    if code_summaries:
        items.extend(f"异常码主因优先按 RAG 手册核对：{summary}" for summary in code_summaries[:3])
    elif fault_codes or alarm_codes:
        items.append("数据库已检出有效异常码，但知识库未命中明确释义；主因需结合厂家手册、参数记录和现场现象确认。")

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
    if fault_codes or alarm_codes:
        items.extend(
            [
                "当前设备是否仍保持该异常码，以及是否已经执行过复位、停机或降载。",
                "异常码出现前后的参数修改记录、单位设置变更记录和功能块激活时间点。",
                "复位或参数恢复前后的状态字、控制字、运行命令和异常码变化。",
            ]
        )
        if not knowledge_artifact.success:
            items.append("知识库缺少该异常码的可靠释义、触发条件和处置步骤。")

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
        items.append("异常码识别：high，SQL 最近样本中存在有效异常码。")
        if knowledge_artifact.success:
            items.append("RAG 释义匹配：high，知识库已返回异常码原因或处理片段。")
        else:
            items.append("RAG 释义匹配：low，知识库未命中明确故障码条目。")
    else:
        items.append("异常码识别：medium，当前样本未见有效异常码，但仍需结合采样覆盖范围判断。")

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
    if fault_codes or alarm_codes:
        notices.append("异常码未闭环前，避免在参数或功能块状态未确认的情况下反复复位、强启或继续带载试运行。")
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

    sql_report = _build_sql_report_summary(sql_artifact)
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
        signal_rows = _build_signal_rows(sql_report.rows, fault_codes, alarm_codes)
        lines.extend(
            [
                f"样本数：{len(sql_report.rows)}",
                f"最新时间：{_format_value(latest.get('create_time'))}",
                f"设备：{', '.join(_unique_non_empty(sql_report.rows, 'device_name')) or request.equipment_hint or '未识别'}",
                f"状态字：{_format_value(latest.get('status'))}",
                f"故障码：{', '.join(fault_codes) if fault_codes else '无'}",
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
        items.append("立即处置：确认当前设备运行/停机状态和现场安全条件；在异常码原因未确认前，避免反复复位、强启或继续带载试运行。")
        if code_summaries:
            for summary in code_summaries:
                items.append(f"故障码处置：按 RAG 手册片段核对触发条件和处理项，操作前记录当前参数快照；{summary}")
        elif knowledge_artifact.success:
            items.append("已自动检索 RAG 知识片段，但片段中未抽取到明确处置步骤；先按数据侧异常特征复核复位条件、运行命令和关键电气量。")
        else:
            items.append("系统已自动检索知识库但未命中明确释义；当前建议先依据数据特征执行现场安全检查，并将故障码释义作为后续知识库补齐项。")
        items.append("验证步骤：记录复位或参数恢复前后的状态字、控制字、母线电压、运行命令和异常码变化，确认故障是否可复现。")
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


def _build_sql_report_summary(sql_artifact: SqlStepArtifact) -> SqlReportSummary:
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
    abnormal_text = (
        f"发现故障码：{', '.join(fault_codes)}"
        if fault_codes
        else "未发现有效故障码"
    )
    if alarm_codes:
        abnormal_text += f"；告警码：{', '.join(alarm_codes)}"
    abnormal_count = sum(1 for row in rows if _is_abnormal_row(row))
    latest_streak = _latest_abnormal_streak(rows)
    oldest_time = _row_time(rows[-1])
    health_level = _derive_health_level(rows, fault_codes, alarm_codes)

    latest_rows = rows[:5]
    state_table = _markdown_table(
        ["时间", "设备", "状态", "故障码", "告警码", "母线电压(V)", "实际转速", "实际电流", "电机温度", "变频器温度"],
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
    abnormal_table = _markdown_table(["时间", "设备", "状态", "故障码", "告警码"], abnormal_rows)
    trend_table = _markdown_table(
        ["指标", "最新", "最小", "最大", "平均", "趋势"],
        _metric_trend_rows(rows),
    )
    status_table = _markdown_table(["状态字", "记录数"], _count_rows(rows, "status"))
    fault_count_table = _markdown_table(["故障码", "记录数"], _count_rows(rows, "fault_code", normalize_code=True))
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
    overview_table = _markdown_table(
        ["维度", "结果"],
        [
            ["数据样本", f"{len(rows)} 条"],
            ["覆盖设备", ", ".join(devices) or "未识别"],
            ["最新记录时间", latest_time],
            ["最新状态字", status],
            ["故障码", ", ".join(fault_codes) if fault_codes else "无"],
            ["告警码", ", ".join(alarm_codes) if alarm_codes else "无"],
        ],
    )

    details = (
        f"### 运行健康判定\n{health_table}\n\n"
        f"### 运行概览\n{overview_table}\n\n"
        f"### 异常特征解读\n{signal_table}\n\n"
        f"### 指标趋势可视化\n{trend_table}\n\n"
        f"### 最新运行快照\n{state_table}\n\n"
        f"### 最新关键指标\n{metric_table}\n\n"
        f"### 状态分布\n{status_table}\n\n"
        f"### 故障码分布\n{fault_count_table}\n\n"
        f"### 异常码与告警码明细\n{abnormal_table}"
    )
    summary = (
        f"已从 {REAL_DATA_LATEST_TABLE} 获取 {len(rows)} 条 DCMA 运行数据，最新设备 {devices[0] if devices else '未知'} "
        f"在 {latest_time} 的状态为 {status}，综合判定：{health_level}；{abnormal_text}。"
    )
    inference_items = [
        f"综合判定为“{health_level}”。",
        f"最近 {len(rows)} 条样本中 {abnormal_count} 条包含有效异常码，最新连续异常 {latest_streak} 条。",
        f"最新状态字为 {status}，控制字/状态字为 {_format_value(latest.get('control_word'))}/{_format_value(latest.get('status_word'))}。",
    ]
    if fault_codes or alarm_codes:
        inference_items.append(f"数据库最近记录中{abnormal_text}，需结合厂家手册确认代码含义和复位条件。")
    else:
        inference_items.append("数据库最近记录未显示有效故障码或告警码，当前更偏向运行状态巡检结论。")
    inference_items.extend(row[1] for row in _build_signal_rows(rows, fault_codes, alarm_codes)[1:4])
    fault_inference = "\n".join(f"- {item}" for item in inference_items)
    maintenance = "\n".join(
        f"- {item}"
        for item in [
            f"将 {REAL_DATA_LATEST_TABLE} 的最新写入时间、写入频率和异常码占比纳入日常健康检查。",
            "为高频异常码建立手册释义、触发条件、复位条件和现场检查项的结构化知识条目。",
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
        chart_payload=_build_chart_payload(rows),
        health_level=health_level,
    )


def _knowledge_report_section(knowledge_artifact: KnowledgeStepArtifact) -> str:
    raw_output = (knowledge_artifact.raw_output or "").strip()
    if not raw_output:
        return "知识库未返回故障码相关内容。"
    if not knowledge_artifact.success:
        return f"知识库检索结果：{raw_output}"
    return raw_output[:2000].strip()


def build_structured_analysis_artifact(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
) -> AnalysisStepArtifact | None:
    sql_report = _build_sql_report_summary(sql_artifact)
    if not sql_report.rows:
        return None

    latest = sql_report.rows[0]
    devices = _unique_non_empty(sql_report.rows, "device_name")
    fault_codes = _unique_codes(sql_report.rows, "fault_code")
    alarm_codes = _unique_codes(sql_report.rows, "alarm_code")
    code_text = ", ".join(fault_codes) if fault_codes else "未见有效故障码"
    alarm_text = ", ".join(alarm_codes) if alarm_codes else "未见有效告警码"
    device_text = ", ".join(devices) or request.equipment_hint or "DCMA 系统"
    latest_time = _format_value(latest.get("create_time"))
    status = _format_value(latest.get("status"))
    conclusion = (
        f"DCMA 最近运行数据已从 {REAL_DATA_LATEST_TABLE} 获取，{device_text} 最新记录状态为 {status}，"
        f"综合判定：{sql_report.health_level}；故障码为 {code_text}，告警码为 {alarm_text}。"
    )
    if knowledge_artifact.success:
        conclusion += " 已自动补充知识库检索结果用于诊断说明。"
    elif fault_codes:
        conclusion += " 已自动查询知识库，但当前知识库未命中该故障码的明确释义。"

    basis = [
        f"SQL 返回 {len(sql_report.rows)} 条 {REAL_DATA_LATEST_TABLE} 最近运行记录。",
        f"最新记录时间 {latest_time}，设备 {device_text}，状态 {status}。",
        f"故障码统计：{code_text}；告警码统计：{alarm_text}。",
        f"运行健康判定：{sql_report.health_level}。",
        (
            "知识库已返回故障码相关片段。"
            if knowledge_artifact.success
            else "知识库未返回该故障码的明确片段。"
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
) -> dict[str, str]:
    sql_report = _build_sql_report_summary(sql_artifact)
    executive_summary = analysis_artifact.conclusion
    if sql_report.rows:
        executive_summary = f"{analysis_artifact.conclusion}\n\n{sql_report.summary}"

    return {
        "title": "DCMA 故障诊断报告",
        "report_time": current_time,
        "diagnosis_object": request.equipment_hint or "DCMA 系统",
        "diagnosis_type": request.fault_code_hint or "故障诊断",
        "executive_summary": executive_summary,
        "diagnosis_overview": (
            f"本报告由限制型单 Agent 生成，已按受控 SQL 查询 {REAL_DATA_LATEST_TABLE} 最近运行数据，"
            "并结合可用知识检索结果、指标趋势和诊断规则形成结论。"
        ),
        "diagnosis_details": (
            f"{sql_report.details_markdown}\n\n"
            f"### RAG 故障码知识补充\n{_knowledge_report_section(knowledge_artifact)}"
        ),
        "fault_inference": f"{analysis_artifact.conclusion}\n\n{sql_report.fault_inference}",
        "repair_recommendations": "\n".join(f"- {item}" for item in analysis_artifact.recommendations)
        or "- 暂无具体处置建议",
        "preventive_maintenance": sql_report.maintenance,
        "diagnosis_basis": (
            f"SQL 摘要：{sql_artifact.summary}\n"
            f"SQL 语句：{'; '.join(sql_artifact.sql_used) or '无'}\n"
            f"SQL 返回：{len(sql_report.rows)} 条可解析 {REAL_DATA_LATEST_TABLE} 行数据\n"
            f"知识查询：{knowledge_artifact.query or '无'}\n"
            f"分析依据：{'; '.join(analysis_artifact.basis) or '无'}"
        ),
        "report_filename": report_filename,
        "chart_payload": sql_report.chart_payload,
    }
