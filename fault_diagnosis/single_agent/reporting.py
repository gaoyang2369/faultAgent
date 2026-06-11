"""Report payload and final-answer formatting helpers."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
)
from .sql_safety import REAL_DATA_FALLBACK_COLUMN_NAMES

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


@dataclass
class SqlReportSummary:
    rows: list[dict[str, object]]
    summary: str
    details_markdown: str
    fault_inference: str
    maintenance: str


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


def _build_sql_report_summary(sql_artifact: SqlStepArtifact) -> SqlReportSummary:
    rows = _parse_sql_rows(sql_artifact.raw_output or sql_artifact.result_preview)
    if not rows:
        return SqlReportSummary(
            rows=[],
            summary="SQL 查询未返回可解析的 real_data 行数据。",
            details_markdown=f"【SQL 结果摘要】\n{sql_artifact.result_preview or sql_artifact.raw_output or '无'}",
            fault_inference="当前报告无法基于数据库行数据确认运行状态，请先核对 SQL 查询条件和数据库连接。",
            maintenance="建议先确认 real_data 最近数据是否可查询，再补充设备范围或时间范围后重新生成报告。",
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
        f"### 运行概览\n{overview_table}\n\n"
        f"### 指标趋势可视化\n{trend_table}\n\n"
        f"### 最新运行快照\n{state_table}\n\n"
        f"### 最新关键指标\n{metric_table}\n\n"
        f"### 状态分布\n{status_table}\n\n"
        f"### 故障码分布\n{fault_count_table}\n\n"
        f"### 异常码与告警码明细\n{abnormal_table}"
    )
    summary = (
        f"已获取 {len(rows)} 条 DCMA 运行数据，最新设备 {devices[0] if devices else '未知'} "
        f"在 {latest_time} 的状态字为 {status}，{abnormal_text}。"
    )
    fault_inference = (
        f"数据库最近记录中{abnormal_text}。"
        if fault_codes or alarm_codes
        else "数据库最近记录未显示有效故障码或告警码，当前更偏向运行状态巡检结论。"
    )
    maintenance = (
        "建议优先结合设备手册核对故障码含义，并检查状态字、控制字、母线电压、温度和负载率是否与现场现象一致。"
        if fault_codes or alarm_codes
        else "建议继续跟踪状态字、温度、母线电压、负载率和功率指标，若出现非零故障码或告警码再进入故障排查流程。"
    )
    return SqlReportSummary(rows=rows, summary=summary, details_markdown=details, fault_inference=fault_inference, maintenance=maintenance)


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
        f"DCMA 最近运行数据已获取，{device_text} 最新记录状态字为 {status}，"
        f"故障码为 {code_text}，告警码为 {alarm_text}。"
    )
    if knowledge_artifact.success:
        conclusion += " 已自动补充知识库检索结果用于报告说明。"
    elif fault_codes:
        conclusion += " 已自动查询知识库，但当前知识库未命中该故障码的明确释义。"

    basis = [
        f"SQL 返回 {len(sql_report.rows)} 条 real_data 最近运行记录。",
        f"最新记录时间 {latest_time}，设备 {device_text}，状态字 {status}。",
        f"故障码统计：{code_text}；告警码统计：{alarm_text}。",
        (
            "知识库已返回故障码相关片段。"
            if knowledge_artifact.success
            else "知识库未返回该故障码的明确片段。"
        ),
    ]
    if fault_codes and knowledge_artifact.success:
        knowledge_recommendation = "结合报告中的 RAG 检索结果核对故障码含义、触发条件和处理步骤。"
    elif fault_codes:
        knowledge_recommendation = "系统已自动检索知识库但未命中该故障码，建议导入对应厂家手册或故障码表后重新生成报告。"
    else:
        knowledge_recommendation = "当前未见有效故障码，建议继续跟踪状态字、温度、负载率和功率指标。"

    recommendations = [
        knowledge_recommendation,
        "现场复核状态字、控制字、母线电压、温度、负载率和功率指标是否与设备现象一致。",
        "若确认故障码持续出现，按设备手册流程执行停机安全检查、复位条件确认和试运行观察。",
        "演示后建议接入持续采集任务，将 real_data 写入频率和最新采集时间纳入健康检查。",
    ]

    return AnalysisStepArtifact(
        success=True,
        conclusion=conclusion,
        basis=basis,
        recommendations=recommendations,
        risk_notice="如现场设备处于运行状态，复位或试运行前应先完成安全确认。",
        missing_information=[] if knowledge_artifact.success or not fault_codes else ["知识库未命中故障码释义"],
        confidence="high" if knowledge_artifact.success else "medium",
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
            "本报告由限制型单 Agent 生成，已按受控 SQL 查询 real_data 最近运行数据，"
            "并结合可用知识检索结果与诊断规则形成结论。"
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
            f"SQL 返回：{len(sql_report.rows)} 条可解析 real_data 行数据\n"
            f"知识查询：{knowledge_artifact.query or '无'}\n"
            f"分析依据：{'; '.join(analysis_artifact.basis) or '无'}"
        ),
        "report_filename": report_filename,
    }


def build_final_answer_prompt(analysis_artifact: AnalysisStepArtifact, report_name: str) -> str:
    return f"""
你是 DCMA 限制型单 Agent 的最终答复整理器。
请用中文生成最终用户答复，结构清晰，必须包含：
1. 一句话结论
2. 数据支撑
3. 处理建议
4. 风险提示或不确定性
5. 报告文件名

结论：{analysis_artifact.conclusion}
依据：{analysis_artifact.basis}
建议：{analysis_artifact.recommendations}
风险提示：{analysis_artifact.risk_notice}
缺失信息：{analysis_artifact.missing_information}
置信度：{analysis_artifact.confidence}
报告文件名：{report_name}
""".strip()


def build_final_answer_fallback(analysis_artifact: AnalysisStepArtifact, report_name: str) -> str:
    basis_lines = "\n".join(f"- {item}" for item in analysis_artifact.basis) or "- 暂无明确数据支撑"
    recommendation_lines = "\n".join(f"- {item}" for item in analysis_artifact.recommendations) or "- 暂无具体处置建议"
    risk_notice = analysis_artifact.risk_notice or "当前未发现额外风险提示。"
    return (
        f"【结论】{analysis_artifact.conclusion}\n"
        f"【数据支撑】\n{basis_lines}\n"
        f"【处置建议】\n{recommendation_lines}\n"
        f"【风险提示】{risk_notice}\n"
        f"【报告文件】{report_name}"
    )
