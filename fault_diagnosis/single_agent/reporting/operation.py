"""Structured operation-diagnosis report model and rule builder."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    WorkOrderSuggestion,
)
from .defs import (
    DC_VOLTAGE_LOWER,
    DC_VOLTAGE_UPPER,
    INVERTER_TEMP_CRITICAL,
    INVERTER_TEMP_WARNING,
    LOAD_CRITICAL,
    LOAD_WARNING,
    MOTOR_TEMP_CRITICAL,
    MOTOR_TEMP_WARNING,
    SPEED_ERROR_CRITICAL_PERCENT,
    SPEED_ERROR_WARNING_PERCENT,
)
from .utils import (
    format_float as _format_float,
    format_value as _format_value,
    latest_code_streak as _latest_code_streak,
    metric_max as _metric_max,
    metric_values as _metric_values,
    normalize_code as _normalize_code,
    speed_deviation_percent as _speed_deviation_percent,
    to_float as _to_float,
    unique_codes as _unique_codes,
    unique_non_empty as _unique_non_empty,
)
from ..sql_safety import REAL_DATA_LATEST_TABLE, is_generic_equipment_hint


class ReportSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    NOTICE = "notice"
    NORMAL = "normal"
    UNKNOWN = "unknown"


class DataCurrentnessLevel(str, Enum):
    REALTIME = "realtime"
    RECENT = "recent"
    STALE = "stale"
    MISSING = "missing"


class ReportKpiCard(BaseModel):
    name: str
    value: str
    latest_value: str | None = None
    window_max: str | None = None
    reference: str | None = None
    judgement: str | None = None
    severity: ReportSeverity
    evidence_source: str | None = None
    evidence_id: str | None = None


class ReportFinding(BaseModel):
    finding_id: str
    title: str
    severity: ReportSeverity
    evidence_summary: str
    impact: str
    engineering_meaning: str | None = None
    supporting_evidence: str | None = None
    missing_evidence: str | None = None
    confidence: str
    evidence_ids: list[str] = Field(default_factory=list)


class CauseCandidate(BaseModel):
    rank: int
    cause: str
    confidence: str
    supporting_evidence: list[str]
    counter_evidence_or_uncertainty: list[str]
    verification_step: str
    conclusion_if_verified: str | None = None


class ActionItem(BaseModel):
    priority: str
    action: str
    owner_role: str
    trigger_or_due: str
    purpose: str
    acceptance_criteria: str | None = None
    escalation_condition: str | None = None
    safety_note: str | None = None


class ReportAppendix(BaseModel):
    sql_summary: str | None = None
    sql_query: str | None = None
    raw_metric_tables: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_sources: list[dict[str, Any]] = Field(default_factory=list)
    control_status_decode: dict[str, Any] | None = None
    trend_statistics: list[dict[str, Any]] = Field(default_factory=list)
    generation_metadata: dict[str, Any] = Field(default_factory=dict)


class OperationDiagnosisReport(BaseModel):
    title: str
    report_time: str
    asset: str
    report_type: str
    data_window: str
    sample_count: int | None = None
    data_age_text: str
    data_freshness_label: str
    data_freshness_note: str
    data_currentness_level: DataCurrentnessLevel
    data_currentness_label: str
    asset_risk_level: ReportSeverity
    asset_risk_label: str
    action_priority: str
    action_priority_label: str
    confidence_level: str
    severity: ReportSeverity
    severity_label: str
    confidence: str
    event_code: str | None = None
    one_sentence_conclusion: str
    top_actions: list[str]
    kpi_cards: list[ReportKpiCard]
    findings: list[ReportFinding]
    cause_candidates: list[CauseCandidate]
    action_plan: list[ActionItem]
    workorder_suggestion: dict[str, Any]
    evidence_summary: list[dict[str, str]]
    limitations: list[str]
    appendix: ReportAppendix


_SEVERITY_RANK = {
    ReportSeverity.UNKNOWN: 0,
    ReportSeverity.NORMAL: 1,
    ReportSeverity.NOTICE: 2,
    ReportSeverity.WARNING: 3,
    ReportSeverity.HIGH: 4,
    ReportSeverity.CRITICAL: 5,
}

_SEVERITY_LABELS = {
    ReportSeverity.CRITICAL: "CRITICAL / 严重",
    ReportSeverity.HIGH: "HIGH / 高风险",
    ReportSeverity.WARNING: "WARNING / 采样窗口异常",
    ReportSeverity.NOTICE: "NOTICE / 提示",
    ReportSeverity.NORMAL: "NORMAL / 正常",
    ReportSeverity.UNKNOWN: "UNKNOWN / 无法判断",
}

_DATA_CURRENTNESS_LABELS = {
    DataCurrentnessLevel.REALTIME: "REALTIME / 可代表实时状态",
    DataCurrentnessLevel.RECENT: "RECENT / 近期可参考",
    DataCurrentnessLevel.STALE: "STALE / 不代表实时状态",
    DataCurrentnessLevel.MISSING: "MISSING / 缺少运行数据",
}

_CRITICAL_WORDS = ("停机", "急停", "禁止运行", "禁止继续运行", "保护动作", "立即停机", "隔离")
_KNOWLEDGE_ACTION_LABELS = ("含义", "说明", "反应", "原因", "触发", "处理", "排除", "措施", "检查", "维修", "复位")
_KNOWLEDGE_SOURCE_PREFIXES = ("来源", "source_type", "file_id", "extract_backend", "corrected", "correction_source", "检索方式", "故障码")


def _speed_deviation_values(rows: list[dict[str, object]]) -> list[float]:
    values = []
    for row in reversed(rows):
        value = _speed_deviation_percent(row)
        if value is not None:
            values.append(value)
    return values


def _event_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for key in ("fault_code", "alarm_code"):
            code = _normalize_code(row.get(key))
            if code:
                counts[code] = counts.get(code, 0) + 1
    return counts


def _sql_has_device_filter(sql_statement: str) -> bool:
    normalized = str(sql_statement or "").lower()
    matched = re.search(r"\bwhere\b(?P<where>.*?)(?:\border\s+by\b|\blimit\b|$)", normalized, re.DOTALL)
    where_clause = matched.group("where") if matched else ""
    return bool(re.search(r"\b(?:device_name|inverter_name)\b\s*(?:=|in\b|like\b)", where_clause))


def _covered_devices_text(rows: list[dict[str, object]]) -> str:
    return ", ".join(_unique_non_empty(rows, "device_name")) or "未识别"


def _report_asset_label(
    request: DiagnosisRequest,
    rows: list[dict[str, object]],
    status_summary: dict[str, object],
    sql_statement: str,
) -> str:
    devices_text = _covered_devices_text(rows)
    if _sql_has_device_filter(sql_statement):
        request_asset = (request.equipment_hint or "").strip()
        if request_asset and not is_generic_equipment_hint(request_asset):
            return request_asset
        return devices_text if devices_text != "未识别" else str(status_summary.get("device") or "DCMA 系统")
    suffix = f"（覆盖设备：{devices_text}）" if devices_text != "未识别" else ""
    return f"{REAL_DATA_LATEST_TABLE} 最新采样窗口{suffix}"


def _risk_label(severity: ReportSeverity, currentness_level: DataCurrentnessLevel) -> str:
    if _is_stale_data(currentness_level):
        return f"采样窗口风险 {severity.name} / 当前状态 UNKNOWN"
    return _SEVERITY_LABELS[severity]


def _highest(*severities: ReportSeverity) -> ReportSeverity:
    if not severities:
        return ReportSeverity.UNKNOWN
    return max(severities, key=lambda item: _SEVERITY_RANK[item])


def _high_threshold_severity(value: float | None, warning: float, critical: float) -> ReportSeverity:
    if value is None:
        return ReportSeverity.UNKNOWN
    if value >= critical:
        return ReportSeverity.HIGH
    if value >= warning:
        return ReportSeverity.WARNING
    return ReportSeverity.NORMAL


def _severity_short_label(severity: ReportSeverity) -> str:
    return {
        ReportSeverity.CRITICAL: "CRITICAL",
        ReportSeverity.HIGH: "HIGH",
        ReportSeverity.WARNING: "WARNING",
        ReportSeverity.NOTICE: "NOTICE",
        ReportSeverity.NORMAL: "NORMAL",
        ReportSeverity.UNKNOWN: "UNKNOWN",
    }[severity]


def _high_threshold_judgement(value: float | None, warning: float, critical: float, unit: str = "") -> str:
    if value is None:
        return "缺少数据，无法判定"
    value_text = f"{_format_float(value)}{unit}"
    if value >= critical:
        return f"{value_text}，达到或超过高危阈值"
    if value >= critical * 0.9:
        return f"{value_text}，接近高危阈值，需优先确认"
    if value >= warning:
        return f"{value_text}，进入关注区间"
    return f"{value_text}，未超过关注阈值"


def _voltage_severity(value: float | None) -> ReportSeverity:
    if value is None:
        return ReportSeverity.UNKNOWN
    if value < DC_VOLTAGE_LOWER or value > DC_VOLTAGE_UPPER:
        return ReportSeverity.WARNING
    return ReportSeverity.NORMAL


def _has_critical_language(*texts: object) -> bool:
    joined = " ".join(str(text or "") for text in texts)
    return any(word in joined for word in _CRITICAL_WORDS)


def _is_source_metadata_line(line: str) -> bool:
    normalized = line.strip()
    return any(normalized.startswith(prefix) for prefix in _KNOWLEDGE_SOURCE_PREFIXES)


def _line_has_knowledge_action(line: str) -> bool:
    normalized = line.strip()
    return any(label in normalized for label in _KNOWLEDGE_ACTION_LABELS)


def _knowledge_blocks(knowledge_artifact: KnowledgeStepArtifact) -> list[str]:
    if knowledge_artifact.snippets:
        return [str(item).strip() for item in knowledge_artifact.snippets if str(item).strip()]
    return [item.strip() for item in (knowledge_artifact.raw_output or "").split("\n\n") if item.strip()]


def _knowledge_action_summaries(
    knowledge_artifact: KnowledgeStepArtifact,
    codes: list[str],
    *,
    per_code_limit: int = 4,
) -> list[str]:
    if not knowledge_artifact.success:
        return []
    summaries: list[str] = []
    target_codes = [code.upper() for code in codes if code]
    blocks = _knowledge_blocks(knowledge_artifact)
    if not target_codes:
        target_codes = [""]
    for code in target_codes:
        selected_lines: list[str] = []
        for block in blocks:
            if code and code not in block.upper():
                continue
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


def _overall_severity(
    *,
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
    kpi_cards: list[ReportKpiCard],
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    data_currentness: DataCurrentnessLevel,
) -> ReportSeverity:
    if not rows:
        return ReportSeverity.UNKNOWN
    if fault_codes:
        if _has_critical_language(knowledge_artifact.raw_output, analysis_artifact.conclusion, analysis_artifact.recommendations):
            return ReportSeverity.CRITICAL
        return ReportSeverity.HIGH
    kpi_severity = _highest(*(card.severity for card in kpi_cards))
    if (
        _SEVERITY_RANK[kpi_severity] >= _SEVERITY_RANK[ReportSeverity.HIGH]
        and not (alarm_codes and _is_stale_data(data_currentness))
    ):
        return kpi_severity
    if alarm_codes:
        return _highest(ReportSeverity.WARNING, kpi_severity)
    return kpi_severity


def _confidence(
    severity: ReportSeverity,
    data_quality: dict[str, object],
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
) -> str:
    if severity == ReportSeverity.UNKNOWN:
        return "低"
    label = str(data_quality.get("freshness_label") or "")
    if label and label != "实时性良好":
        return "中"
    if knowledge_artifact.success and str(analysis_artifact.confidence or "").lower() in {"high", "medium"}:
        return "高"
    return "中"


def _data_age_text(freshness_seconds: object) -> str:
    if not isinstance(freshness_seconds, (int, float)):
        return "未评估"
    seconds = max(0.0, float(freshness_seconds))
    if seconds < 60:
        return f"{_format_float(seconds, 0)} 秒"
    minutes = seconds / 60
    if minutes < 60:
        return f"{_format_float(minutes, 1)} 分钟"
    hours = minutes / 60
    if hours < 48:
        return f"{_format_float(hours, 1)} 小时"
    return f"{_format_float(hours / 24, 1)} 天"


def _data_currentness_level(data_quality: dict[str, object], rows: list[dict[str, object]]) -> DataCurrentnessLevel:
    if not rows:
        return DataCurrentnessLevel.MISSING
    freshness_seconds = data_quality.get("freshness_seconds")
    if not isinstance(freshness_seconds, (int, float)):
        return DataCurrentnessLevel.MISSING
    if freshness_seconds <= 5 * 60:
        return DataCurrentnessLevel.REALTIME
    if freshness_seconds <= 60 * 60:
        return DataCurrentnessLevel.RECENT
    return DataCurrentnessLevel.STALE


def _is_stale_data(level: DataCurrentnessLevel) -> bool:
    return level in {DataCurrentnessLevel.STALE, DataCurrentnessLevel.MISSING}


def _action_priority(asset_risk: ReportSeverity, data_currentness: DataCurrentnessLevel) -> tuple[str, str]:
    if asset_risk == ReportSeverity.CRITICAL and data_currentness == DataCurrentnessLevel.REALTIME:
        return "P0", "立即停机/隔离/安全处置"
    if _is_stale_data(data_currentness):
        return "P1", "立即确认实时数据与现场状态"
    if asset_risk in {ReportSeverity.CRITICAL, ReportSeverity.HIGH, ReportSeverity.WARNING}:
        return "P2", "本班次排查"
    return "P3", "计划性维护或跟踪"


def _event_summary(rows: list[dict[str, object]], code: str | None) -> str:
    if not rows or not code:
        return "未见有效事件码/故障码"
    counts = _event_counts(rows)
    count = counts.get(code, 0)
    streak = _latest_code_streak(rows, code)
    suffix = "持续出现" if count == len(rows) else "间歇出现"
    return f"{code} 出现 {count}/{len(rows)}，最新连续 {streak} 条，{suffix}"


def _build_window_conclusion(
    *,
    rows: list[dict[str, object]],
    asset: str,
    primary_code: str | None,
    data_currentness: DataCurrentnessLevel,
    data_age_text: str,
    knowledge_artifact: KnowledgeStepArtifact,
) -> str:
    if not rows:
        return "本次报告缺少可解析运行样本，不能判断设备当前或采样窗口内状态。"
    latest = rows[0]
    speed_latest = _speed_deviation_percent(latest)
    speed_max = max(_speed_deviation_values(rows), default=None)
    load_max = _metric_max(rows, "inverter_load_rate", "motor_load_rate")
    code_text = f"持续出现 {primary_code} 事件" if primary_code else "未见有效事件码"
    metric_parts = []
    if speed_latest is not None:
        if speed_max is not None and speed_max > speed_latest:
            metric_parts.append(f"速度偏差率最新 {_format_float(speed_latest)}%、窗口最大 {_format_float(speed_max)}%")
        else:
            metric_parts.append(f"速度偏差率 {_format_float(speed_latest)}%")
    if load_max is not None and load_max >= LOAD_WARNING:
        metric_parts.append(f"负载率最高 {_format_float(load_max)}%")
    metric_text = "，且" + "、".join(metric_parts) if metric_parts else ""
    knowledge_summaries = _knowledge_action_summaries(knowledge_artifact, [primary_code] if primary_code else [])
    knowledge_text = f" 手册要点：{_truncate(knowledge_summaries[0], 64)}。" if knowledge_summaries else ""
    stale_tail = (
        f"；但由于最新样本已滞后约 {data_age_text}，不能直接判断设备当前仍处于异常状态。"
        if _is_stale_data(data_currentness)
        else "。"
    )
    return _truncate(
        f"采样窗口内，{asset}{code_text}{metric_text}。{knowledge_text}需要确认参数/单位制、运行模式和反馈链路{stale_tail}",
        190,
    )


def _truncate(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else f"{clean[: max(0, limit - 1)]}…"


def _build_kpi_cards(rows: list[dict[str, object]]) -> list[ReportKpiCard]:
    if not rows:
        return [
            ReportKpiCard(name="运行数据", value="无可解析样本", reference="SQL 返回结果", severity=ReportSeverity.UNKNOWN)
        ]
    latest = rows[0]
    counts = _event_counts(rows)
    primary_code = next(iter(counts.keys()), "")
    speed_error = _speed_deviation_percent(latest)
    speed_error_max = max(_speed_deviation_values(rows), default=None)
    speed_latest_severity = _high_threshold_severity(
        speed_error,
        SPEED_ERROR_WARNING_PERCENT,
        SPEED_ERROR_CRITICAL_PERCENT,
    )
    speed_window_severity = _high_threshold_severity(
        speed_error_max,
        SPEED_ERROR_WARNING_PERCENT,
        SPEED_ERROR_CRITICAL_PERCENT,
    )
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate")
    latest_load = max(
        value for value in (
            _to_float(latest.get("inverter_load_rate")),
            _to_float(latest.get("motor_load_rate")),
        )
        if value is not None
    ) if any(_to_float(latest.get(key)) is not None for key in ("inverter_load_rate", "motor_load_rate")) else None
    latest_motor_temp = _to_float(latest.get("motor_temp"))
    max_motor_temp = _metric_max(rows, "motor_temp")
    latest_inverter_temp = _to_float(latest.get("inverter_temp"))
    max_inverter_temp = _metric_max(rows, "inverter_temp", "inverter_radiator_temp")
    dc_voltage = _to_float(latest.get("dc_voltage"))
    dc_voltage_values = _metric_values(rows, "dc_voltage")
    dc_voltage_min = min(dc_voltage_values) if dc_voltage_values else None
    dc_voltage_max = max(dc_voltage_values) if dc_voltage_values else None
    cards = [
        ReportKpiCard(
            name="事件码",
            value=(
                f"{primary_code}，{counts[primary_code]}/{len(rows)} 持续"
                if primary_code
                else "无有效事件码/故障码；告警码：无有效告警码"
            ),
            latest_value=primary_code or "无有效告警码",
            window_max=f"{counts[primary_code]}/{len(rows)}" if primary_code else "0",
            reference="持续出现" if primary_code and counts[primary_code] == len(rows) else "样本窗口统计",
            judgement="需核对事件含义、参数变更记录和现场模式" if primary_code else "未触发事件码风险",
            severity=(
                ReportSeverity.HIGH
                if primary_code.upper().startswith("F")
                else ReportSeverity.WARNING
                if primary_code
                else ReportSeverity.NORMAL
            ),
            evidence_source=REAL_DATA_LATEST_TABLE,
            evidence_id="E1",
        ),
        ReportKpiCard(
            name="速度偏差率",
            value=(
                f"最新 {_format_float(speed_error)}%（{_severity_short_label(speed_latest_severity)}），"
                f"窗口最大 {_format_float(speed_error_max)}%（{_severity_short_label(speed_window_severity)}）"
                if speed_error is not None and speed_error_max is not None
                else "缺少速度给定/反馈"
            ),
            latest_value=f"{_format_float(speed_error)}%" if speed_error is not None else None,
            window_max=f"{_format_float(speed_error_max)}%" if speed_error_max is not None else None,
            reference=f"关注 ≥{_format_float(SPEED_ERROR_WARNING_PERCENT)}%，高危 ≥{_format_float(SPEED_ERROR_CRITICAL_PERCENT)}%",
            judgement=(
                f"最新值等级 {_severity_short_label(speed_latest_severity)}；"
                f"窗口峰值等级 {_severity_short_label(speed_window_severity)}。"
                f"{_high_threshold_judgement(speed_error_max, SPEED_ERROR_WARNING_PERCENT, SPEED_ERROR_CRITICAL_PERCENT, '%')}"
            ),
            severity=speed_window_severity,
            evidence_source="speed_setpoint / speed_actual / speed_error_rate",
            evidence_id="E2",
        ),
        ReportKpiCard(
            name="最高负载率",
            value=(
                f"最新 {_format_float(latest_load)}%，窗口最大 {_format_float(max_load)}%"
                if max_load is not None
                else "缺少负载率"
            ),
            latest_value=f"{_format_float(latest_load)}%" if latest_load is not None else None,
            window_max=f"{_format_float(max_load)}%" if max_load is not None else None,
            reference=f"关注 ≥{_format_float(LOAD_WARNING)}%，高危 ≥{_format_float(LOAD_CRITICAL)}%",
            judgement=_high_threshold_judgement(max_load, LOAD_WARNING, LOAD_CRITICAL, "%"),
            severity=_high_threshold_severity(max_load, LOAD_WARNING, LOAD_CRITICAL),
            evidence_source="inverter_load_rate / motor_load_rate",
            evidence_id="E2",
        ),
        ReportKpiCard(
            name="电机温度",
            value=f"{_format_float(latest_motor_temp)}℃ / 最高 {_format_float(max_motor_temp)}℃",
            latest_value=f"{_format_float(latest_motor_temp)}℃" if latest_motor_temp is not None else None,
            window_max=f"{_format_float(max_motor_temp)}℃" if max_motor_temp is not None else None,
            reference=f"关注 ≥{_format_float(MOTOR_TEMP_WARNING)}℃",
            judgement=_high_threshold_judgement(max_motor_temp, MOTOR_TEMP_WARNING, MOTOR_TEMP_CRITICAL, "℃"),
            severity=_high_threshold_severity(max_motor_temp, MOTOR_TEMP_WARNING, MOTOR_TEMP_CRITICAL),
            evidence_source="motor_temp",
            evidence_id="E2",
        ),
        ReportKpiCard(
            name="变频器温度",
            value=f"{_format_float(latest_inverter_temp)}℃ / 最高 {_format_float(max_inverter_temp)}℃",
            latest_value=f"{_format_float(latest_inverter_temp)}℃" if latest_inverter_temp is not None else None,
            window_max=f"{_format_float(max_inverter_temp)}℃" if max_inverter_temp is not None else None,
            reference=f"关注 ≥{_format_float(INVERTER_TEMP_WARNING)}℃",
            judgement=_high_threshold_judgement(max_inverter_temp, INVERTER_TEMP_WARNING, INVERTER_TEMP_CRITICAL, "℃"),
            severity=_high_threshold_severity(max_inverter_temp, INVERTER_TEMP_WARNING, INVERTER_TEMP_CRITICAL),
            evidence_source="inverter_temp / inverter_radiator_temp",
            evidence_id="E2",
        ),
        ReportKpiCard(
            name="母线电压",
            value=(
                f"最新 {_format_float(dc_voltage)}V，窗口范围 {_format_float(dc_voltage_min)}-{_format_float(dc_voltage_max)}V"
                if dc_voltage is not None and dc_voltage_min is not None and dc_voltage_max is not None
                else "缺少母线电压"
            ),
            latest_value=f"{_format_float(dc_voltage)}V" if dc_voltage is not None else None,
            window_max=f"{_format_float(dc_voltage_min)}-{_format_float(dc_voltage_max)}V" if dc_voltage_min is not None and dc_voltage_max is not None else None,
            reference=f"默认参考 {_format_float(DC_VOLTAGE_LOWER)}-{_format_float(DC_VOLTAGE_UPPER)}V",
            judgement="处于默认参考范围" if _voltage_severity(dc_voltage) == ReportSeverity.NORMAL else "超出默认参考范围或缺少数据",
            severity=_voltage_severity(dc_voltage),
            evidence_source="dc_voltage",
            evidence_id="E2",
        ),
    ]
    return cards[:6]


def _build_findings(
    rows: list[dict[str, object]],
    kpi_cards: list[ReportKpiCard],
    knowledge_artifact: KnowledgeStepArtifact,
) -> list[ReportFinding]:
    if not rows:
        return [
            ReportFinding(
                finding_id="F1",
                title="SQL 结果缺少可解析运行样本",
                severity=ReportSeverity.UNKNOWN,
                evidence_summary=f"{REAL_DATA_LATEST_TABLE} 查询结果不可用于运行诊断",
                impact="无法判断当前状态",
                engineering_meaning="运行数据不足，不能形成现场处置判断",
                supporting_evidence="SQL 返回结果",
                missing_evidence="有效运行样本、设备映射、采样链路状态",
                confidence="低",
                evidence_ids=["E1"],
            )
        ]
    counts = _event_counts(rows)
    findings: list[ReportFinding] = []
    if counts:
        code, count = next(iter(counts.items()))
        knowledge_summaries = _knowledge_action_summaries(knowledge_artifact, [code])
        knowledge_summary = knowledge_summaries[0] if knowledge_summaries else ""
        findings.append(
            ReportFinding(
                finding_id="F1",
                title=(
                    f"{code} 手册释义与采样持续性"
                    if knowledge_summary
                    else f"{code} 在 {count}/{len(rows)} 条记录中出现"
                ),
                severity=ReportSeverity.HIGH if code.upper().startswith("F") else ReportSeverity.WARNING,
                evidence_summary=f"{REAL_DATA_LATEST_TABLE} + {'知识库' if knowledge_artifact.success else 'SQL 结果'}",
                impact="需确认事件含义、触发条件和是否影响运行",
                engineering_meaning=(
                    knowledge_summary
                    if knowledge_summary
                    else "参数/单位制/功能块激活相关事件需确认"
                    if code.upper().startswith("A")
                    else "故障码触发条件需按手册确认"
                ),
                supporting_evidence=f"{REAL_DATA_LATEST_TABLE}、知识库 {code}" if knowledge_artifact.success else REAL_DATA_LATEST_TABLE,
                missing_evidence="参数变更记录、现场运行模式、复测结果",
                confidence="高" if count == len(rows) else "中",
                evidence_ids=["E1", "E3"],
            )
        )
    for card in kpi_cards:
        if card.name == "事件码":
            continue
        if card.severity in {ReportSeverity.WARNING, ReportSeverity.HIGH, ReportSeverity.CRITICAL, ReportSeverity.UNKNOWN}:
            findings.append(
                ReportFinding(
                    finding_id=f"F{len(findings) + 1}",
                    title=f"{card.name}：{card.value}",
                    severity=card.severity,
                    evidence_summary=card.reference or card.evidence_id or "运行数据解析",
                    impact=(
                        "可能存在运行模式、限幅、反馈链路或负载变化问题"
                        if card.name == "速度偏差率"
                        else "需确认指标有效性和现场工况"
                        if card.severity != ReportSeverity.UNKNOWN
                        else "降低结论可信度"
                    ),
                    engineering_meaning=card.judgement or "需确认指标有效性",
                    supporting_evidence=card.evidence_source or card.evidence_id or "运行数据解析",
                    missing_evidence=(
                        "运行模式、限速状态、反馈链路检查"
                        if card.name == "速度偏差率"
                        else "现场确认记录、复测样本"
                    ),
                    confidence="中",
                    evidence_ids=[card.evidence_id or "E2"],
                )
            )
        if len(findings) >= 4:
            break
    normal_names = [card.name for card in kpi_cards if card.severity == ReportSeverity.NORMAL and card.name != "事件码"]
    if normal_names and len(findings) < 5:
        findings.append(
            ReportFinding(
                finding_id=f"F{len(findings) + 1}",
                title=f"{'、'.join(normal_names[:3])} 未见明显高位异常",
                severity=ReportSeverity.NORMAL,
                evidence_summary="核心指标趋势和最新样本",
                impact="暂不支持这些指标为主因",
                engineering_meaning="可作为排除过热、供电异常等主因的辅助证据",
                supporting_evidence="温度、电压、电流等趋势",
                missing_evidence="现场负载和柜内环境确认",
                confidence="中",
                evidence_ids=["E2"],
            )
        )
    return findings[:5]


def _build_causes(
    rows: list[dict[str, object]],
    fault_codes: list[str],
    alarm_codes: list[str],
    knowledge_artifact: KnowledgeStepArtifact,
) -> list[CauseCandidate]:
    codes = [*fault_codes, *alarm_codes]
    candidates: list[CauseCandidate] = []
    if codes:
        candidates.append(
            CauseCandidate(
                rank=1,
                cause="事件/故障码对应的参数、配置或功能块触发条件",
                confidence="中" if knowledge_artifact.success else "低-中",
                supporting_evidence=[
                    f"样本窗口出现 {', '.join(codes)}",
                    "知识库已返回相关片段" if knowledge_artifact.success else "知识库释义仍需补齐或人工核对",
                ],
                counter_evidence_or_uncertainty=["未见现场参数变更记录", "A 类事件码不能直接等同机械故障"],
                verification_step="核对厂家手册释义、单位制参数、功能块激活记录和最近参数修改记录。",
                conclusion_if_verified="可将事件归类为参数/配置事件，并按参数恢复或配置确认闭环。",
            )
        )
    if rows and (_speed_deviation_percent(rows[0]) or 0) >= SPEED_ERROR_WARNING_PERCENT:
        candidates.append(
            CauseCandidate(
                rank=len(candidates) + 1,
                cause="运行模式、限速、调试状态或速度反馈链路导致速度偏差",
                confidence="中",
                supporting_evidence=[f"最新速度偏差率 {_format_float(_speed_deviation_percent(rows[0]))}%"],
                counter_evidence_or_uncertainty=["缺少当前控制模式、限幅状态和现场运行命令"],
                verification_step="查看运行模式、控制字、状态字、速度给定来源、限速/限流和反馈链路。",
                conclusion_if_verified="可将速度偏差归入运行模式、限幅或反馈链路问题，并按现场复测确认。",
            )
        )
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") if rows else None
    if max_load is not None and max_load >= LOAD_WARNING:
        candidates.append(
            CauseCandidate(
                rank=len(candidates) + 1,
                cause="机械负载或工艺负载变化",
                confidence="中",
                supporting_evidence=[f"样本最高负载率 {_format_float(max_load)}%"],
                counter_evidence_or_uncertainty=["温度和母线电压未必同步异常", "缺少现场负载变化记录"],
                verification_step="现场检查传动、负载、制动状态、工艺节拍和参数限幅。",
                conclusion_if_verified="可将负载率关注项归入机械/工艺负载变化，并跟踪复测后的负载裕量。",
            )
        )
    if not candidates:
        candidates.append(
            CauseCandidate(
                rank=1,
                cause="当前样本未形成明确原因候选",
                confidence="低",
                supporting_evidence=["未见有效事件码或关键指标越限"],
                counter_evidence_or_uncertainty=["样本窗口和现场现象仍需确认"],
                verification_step="继续采集当前实时数据，并核对设备映射、运行命令和现场状态。",
                conclusion_if_verified="可补充运行状态基线，重新评估是否存在隐性异常。",
            )
        )
    return candidates[:3]


def _build_action_plan(
    rows: list[dict[str, object]],
    data_quality: dict[str, object],
    severity: ReportSeverity,
    fault_codes: list[str],
    alarm_codes: list[str],
) -> list[ActionItem]:
    actions: list[ActionItem] = []
    freshness_label = str(data_quality.get("freshness_label") or "")
    if freshness_label and freshness_label != "实时性良好":
        actions.append(
            ActionItem(
                priority="P1",
                action="重新获取实时数据或确认采样链路",
                owner_role="运维/数据工程师",
                trigger_or_due="立即",
                purpose="避免用过期数据判断当前状态",
                acceptance_criteria="获得最近 5 分钟内有效样本，或确认采样链路停更原因",
                escalation_condition="实时数据仍显示事件持续存在或关键指标超过高危阈值",
            )
        )
    codes = [*fault_codes, *alarm_codes]
    if codes:
        actions.append(
            ActionItem(
                priority="P1" if severity in {ReportSeverity.CRITICAL, ReportSeverity.HIGH} else "P2",
                action="核对事件/故障码释义、单位制参数和功能块激活记录",
                owner_role="电气/自动化工程师",
                trigger_or_due="本班次",
                purpose=f"验证 {', '.join(codes[:3])} 的触发条件",
                acceptance_criteria="确认 p0100/p0349/p0505 状态及最近参数变更记录",
                escalation_condition="存在未经审批的参数变更，或恢复后事件仍持续",
            )
        )
    if rows and (_speed_deviation_percent(rows[0]) or 0) >= SPEED_ERROR_WARNING_PERCENT:
        actions.append(
            ActionItem(
                priority="P2",
                action="核对运行模式、限速、点动、自动/手动状态和反馈链路",
                owner_role="现场工程师",
                trigger_or_due="本班次",
                purpose="判断速度偏差是否为真实运行异常",
                acceptance_criteria="确认运行模式、限速/点动/自动状态和速度反馈链路一致性",
                escalation_condition="速度偏差率连续超过 50% 或反馈链路异常无法排除",
            )
        )
    max_load = _metric_max(rows, "inverter_load_rate", "motor_load_rate") if rows else None
    if max_load is not None and max_load >= LOAD_WARNING:
        actions.append(
            ActionItem(
                priority="P2",
                action="检查传动机构、负载、制动状态和工艺节拍",
                owner_role="维修/工艺工程师",
                trigger_or_due="本班次",
                purpose="确认负载率进入关注区间的现场原因",
                acceptance_criteria="确认传动、制动、工艺负载和参数限幅无异常或已记录异常点",
                escalation_condition="负载率超过 90% 或伴随温度/电流异常",
            )
        )
    actions.append(
        ActionItem(
            priority="P3",
            action="复测速度跟随、负载率、温度、电压和事件码是否恢复",
            owner_role="维修工程师",
            trigger_or_due="参数或现场状态确认后",
            purpose="验证处置效果并形成闭环记录",
            acceptance_criteria="复测样本中事件消失或指标回到关注阈值以下，并形成记录",
            escalation_condition="参数恢复或模式确认后异常仍复现",
        )
    )
    if severity in {ReportSeverity.HIGH, ReportSeverity.CRITICAL} or (
        rows and (_speed_deviation_percent(rows[0]) or 0) >= SPEED_ERROR_CRITICAL_PERCENT
    ):
        actions.append(
            ActionItem(
                priority="P3",
                action="若异常复测后仍持续，生成检查类工单草稿",
                owner_role="值班工程师",
                trigger_or_due="复测触发",
                purpose="防止长期异常运行",
                acceptance_criteria="工单草稿包含设备、问题摘要、证据摘要、建议处理和验收标准",
                escalation_condition="现场工程师确认影响生产或存在设备损伤风险",
            )
        )
    return actions[:5]


def _top_actions(action_plan: list[ActionItem]) -> list[str]:
    return [item.action for item in action_plan[:3]]


def _workorder_suggestion(
    severity: ReportSeverity,
    rows: list[dict[str, object]],
    workorder_suggestion: WorkOrderSuggestion | None,
) -> dict[str, Any]:
    if workorder_suggestion and workorder_suggestion.need_workorder:
        return {
            "decision": "建议创建",
            "trigger": "已满足工单草稿生成条件或用户明确要求",
            "can_generate_draft": "是",
            "trigger_conditions": [
                "实时复测后事件仍持续出现",
                "速度偏差率连续超过 50%",
                "负载率超过 90% 或伴随温度/电流异常",
                "参数恢复或模式确认后异常仍复现",
                "现场工程师确认影响生产或存在设备损伤风险",
            ],
            "draft": workorder_suggestion.model_dump(exclude_none=True),
        }
    speed_error = _speed_deviation_percent(rows[0]) if rows else None
    if severity in {ReportSeverity.HIGH, ReportSeverity.CRITICAL} or (speed_error or 0) >= SPEED_ERROR_CRITICAL_PERCENT:
        return {
            "decision": "需要人工确认",
            "trigger": "严重等级或关键指标接近高风险条件",
            "can_generate_draft": "待实时数据确认后生成",
            "trigger_conditions": [
                "实时复测后事件仍持续出现",
                "速度偏差率连续超过 50%",
                "负载率超过 90% 或伴随温度/电流异常",
                "参数恢复或模式确认后异常仍复现",
                "现场工程师确认影响生产或存在设备损伤风险",
            ],
            "note": "建议由有权限人员确认实时数据和现场状态后生成工单草稿。",
        }
    return {
        "decision": "暂不创建维修工单，建议创建检查/确认类任务或等待复测结果",
        "trigger": "当前证据更适合先做实时数据、参数和运行模式确认",
        "can_generate_draft": "否 / 待实时数据确认后生成",
        "trigger_conditions": [
            "实时复测后事件仍持续出现",
            "速度偏差率连续超过 50%",
            "负载率超过 90% 或伴随温度/电流异常",
            "参数恢复或模式确认后异常仍复现",
            "现场工程师确认影响生产或存在设备损伤风险",
        ],
        "note": "若复测后事件持续，或速度偏差超过 50%，再生成检查类工单草稿。",
    }


def _evidence_summary(
    rows: list[dict[str, object]],
    data_quality: dict[str, object],
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
) -> list[dict[str, str]]:
    counts = _event_counts(rows)
    primary_code = next(iter(counts.keys()), "")
    return [
        {
            "type": "运行数据",
            "source": REAL_DATA_LATEST_TABLE,
            "summary": (
                f"{len(rows)} 条样本，{primary_code} 出现 {counts[primary_code]}/{len(rows)}"
                if rows and primary_code
                else f"{len(rows)} 条样本，未见有效异常码"
                if rows
                else "未返回可解析样本"
            ),
            "key_fact": (
                f"{_event_summary(rows, primary_code)}"
                if rows and primary_code
                else "未见有效事件码"
                if rows
                else "未返回可解析样本"
            ),
            "quality": str(data_quality.get("currentness") or data_quality.get("freshness_label") or "未评估"),
            "supports_conclusion": "是" if rows else "否",
            "gap": "实时数据、现场模式、参数变更记录",
        },
        {
            "type": "指标趋势",
            "source": "SQL 结果解析",
            "summary": "; ".join(str(item) for item in analysis_artifact.basis[:2]) or "已生成核心指标趋势",
            "key_fact": "; ".join(str(item) for item in analysis_artifact.basis[:2]) or "已生成核心指标趋势",
            "quality": "可用" if rows else "不可用",
            "supports_conclusion": "是" if rows else "否",
            "gap": "运行模式、限幅状态、反馈链路检查",
        },
        {
            "type": "手册知识",
            "source": "RAG 知识库",
            "summary": "已返回事件码/故障码片段" if knowledge_artifact.success else "未命中明确知识片段",
            "key_fact": "已返回事件码/故障码片段" if knowledge_artifact.success else "未命中明确知识片段",
            "quality": "可用" if knowledge_artifact.success else "不足",
            "supports_conclusion": "部分支持" if knowledge_artifact.success else "否",
            "gap": "完整厂家手册条目、参数上下文",
        },
        {
            "type": "缺失证据",
            "source": "当前系统",
            "summary": "缺少现场运行模式、参数变更记录、复位/复测结果和实时确认",
            "key_fact": "缺少现场运行模式、参数变更记录、复位/复测结果和实时确认",
            "quality": "影响结论置信度",
            "supports_conclusion": "限制结论",
            "gap": "需补齐后才能判断当前实时状态",
        },
    ]


def _build_appendix(
    rows: list[dict[str, object]],
    sql_summary: str,
    sql_statement: str,
    knowledge_artifact: KnowledgeStepArtifact,
    report_time: str,
) -> ReportAppendix:
    latest_rows = rows[:10]
    raw_metric_tables = [
        {
            "title": "最新采样明细",
            "rows": latest_rows,
        }
    ] if latest_rows else []
    knowledge_sources = [
        {
            "query": knowledge_artifact.query or "",
            "raw_excerpt": (knowledge_artifact.raw_output or "")[:2000],
        }
    ] if knowledge_artifact.raw_output else []
    latest = rows[0] if rows else {}
    trend_statistics = []
    for key, label in (
        ("speed_setpoint", "给定转速"),
        ("speed_actual", "实际转速"),
        ("inverter_load_rate", "变频器负载率"),
        ("motor_load_rate", "电机负载率"),
        ("motor_temp", "电机温度"),
        ("inverter_temp", "变频器温度"),
        ("dc_voltage", "母线电压"),
    ):
        values = _metric_values(rows, key)
        if values:
            trend_statistics.append(
                {
                    "name": label,
                    "latest": _format_float(values[-1]),
                    "min": _format_float(min(values)),
                    "max": _format_float(max(values)),
                    "average": _format_float(sum(values) / len(values)),
                }
            )
    return ReportAppendix(
        sql_summary=sql_summary,
        sql_query=sql_statement,
        raw_metric_tables=raw_metric_tables,
        knowledge_sources=knowledge_sources,
        control_status_decode={
            "control_word": _format_value(latest.get("control_word")),
            "status_word": _format_value(latest.get("status_word")),
            "note": "通用解析，需以现场 PLC 映射表为准",
        }
        if latest
        else None,
        trend_statistics=trend_statistics,
        generation_metadata={
            "report_time": report_time,
            "system": "工业设备故障诊断专家系统",
            "note": "报告由结构化规则、采样窗口和知识库摘要生成，主报告已隐藏完整 SQL 与长证据。",
        },
    )


def build_operation_diagnosis_report(
    *,
    request: DiagnosisRequest,
    title: str,
    report_time: str,
    diagnosis_type: str,
    rows: list[dict[str, object]],
    data_quality: dict[str, object],
    status_summary: dict[str, object],
    sql_summary: str,
    sql_statement: str,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion | None,
) -> OperationDiagnosisReport:
    asset = _report_asset_label(request, rows, status_summary, sql_statement)
    unfiltered_window = not _sql_has_device_filter(sql_statement)
    data_window = (
        f"{data_quality.get('oldest_sample_time', '-')} ~ {data_quality.get('latest_sample_time', '-')}"
        if rows
        else "无可解析样本"
    )
    freshness_label = str(data_quality.get("freshness_label") or "未知")
    freshness_seconds = data_quality.get("freshness_seconds")
    data_age_text = _data_age_text(freshness_seconds)
    currentness_level = _data_currentness_level(data_quality, rows)
    freshness_note = (
        f"最新样本距报告时间约 {data_age_text}，数据已滞后，仅代表采样窗口。"
        if freshness_label != "实时性良好"
        else str(data_quality.get("currentness") or "可作为当前状态的强参考")
    )
    kpi_cards = _build_kpi_cards(rows)
    raw_fault_field_codes = _unique_codes(rows, "fault_code")
    raw_alarm_field_codes = _unique_codes(rows, "alarm_code")
    fault_codes = [code for code in raw_fault_field_codes if code.upper().startswith("F")]
    alarm_codes = [
        code
        for code in [*raw_alarm_field_codes, *raw_fault_field_codes]
        if code and not code.upper().startswith("F")
    ]
    severity = _overall_severity(
        rows=rows,
        fault_codes=fault_codes,
        alarm_codes=alarm_codes,
        kpi_cards=kpi_cards,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        data_currentness=currentness_level,
    )
    severity_label = _SEVERITY_LABELS[severity]
    action_plan = _build_action_plan(rows, data_quality, severity, fault_codes, alarm_codes)
    action_priority, action_priority_label = _action_priority(severity, currentness_level)
    primary_code = next(iter(_event_counts(rows).keys()), None)
    conclusion = _build_window_conclusion(
        rows=rows,
        asset=asset,
        primary_code=primary_code,
        data_currentness=currentness_level,
        data_age_text=data_age_text,
        knowledge_artifact=knowledge_artifact,
    )
    limitations = [
        "本报告基于本次采样窗口、知识库和确定性规则生成，仅用于辅助诊断。",
        "由于最新样本已滞后，结论不能直接等同于当前实时状态。",
        "涉及停机、复位、参数恢复等动作，必须由有权限人员按现场规程确认。",
    ]
    if unfiltered_window:
        limitations.insert(
            1,
            f"本报告 SQL 未限定单设备，报告对象为 {REAL_DATA_LATEST_TABLE} 最新采样窗口；覆盖设备：{_covered_devices_text(rows)}。",
        )
    return OperationDiagnosisReport(
        title=title,
        report_time=report_time,
        asset=asset,
        report_type=diagnosis_type,
        data_window=data_window,
        sample_count=len(rows) if rows else 0,
        data_age_text=data_age_text,
        data_freshness_label=freshness_label,
        data_freshness_note=freshness_note,
        data_currentness_level=currentness_level,
        data_currentness_label=_DATA_CURRENTNESS_LABELS[currentness_level],
        asset_risk_level=severity,
        asset_risk_label=_risk_label(severity, currentness_level),
        action_priority=action_priority,
        action_priority_label=action_priority_label,
        confidence_level=_confidence(severity, data_quality, knowledge_artifact, analysis_artifact),
        severity=severity,
        severity_label=severity_label,
        confidence=_confidence(severity, data_quality, knowledge_artifact, analysis_artifact),
        event_code=primary_code,
        one_sentence_conclusion=conclusion,
        top_actions=_top_actions(action_plan),
        kpi_cards=kpi_cards,
        findings=_build_findings(rows, kpi_cards, knowledge_artifact),
        cause_candidates=_build_causes(rows, fault_codes, alarm_codes, knowledge_artifact),
        action_plan=action_plan,
        workorder_suggestion=_workorder_suggestion(severity, rows, workorder_suggestion),
        evidence_summary=_evidence_summary(rows, data_quality, knowledge_artifact, analysis_artifact),
        limitations=limitations,
        appendix=_build_appendix(rows, sql_summary, sql_statement, knowledge_artifact, report_time),
    )
