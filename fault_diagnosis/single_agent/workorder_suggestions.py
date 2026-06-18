"""Work-order suggestion construction for diagnosis results."""

from __future__ import annotations

from typing import Any

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from .reporting_defs import (
    DC_VOLTAGE_LOWER as _DC_VOLTAGE_LOWER,
    DC_VOLTAGE_UPPER as _DC_VOLTAGE_UPPER,
    INVERTER_TEMP_CRITICAL as _INVERTER_TEMP_CRITICAL,
    INVERTER_TEMP_WARNING as _INVERTER_TEMP_WARNING,
    LOAD_CRITICAL as _LOAD_CRITICAL,
    LOAD_WARNING as _LOAD_WARNING,
    MOTOR_TEMP_CRITICAL as _MOTOR_TEMP_CRITICAL,
    MOTOR_TEMP_WARNING as _MOTOR_TEMP_WARNING,
    SPEED_ERROR_CRITICAL_PERCENT as _SPEED_ERROR_CRITICAL_PERCENT,
    SPEED_ERROR_WARNING_PERCENT as _SPEED_ERROR_WARNING_PERCENT,
)


def _reporting_helpers():
    from . import reporting

    return reporting


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
    reporting = _reporting_helpers()
    summaries = reporting._knowledge_action_summaries(knowledge_artifact, codes, per_code_limit=2)
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
    reporting = _reporting_helpers()
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
    return reporting._dedupe_items(steps)


def _workorder_acceptance_criteria(
    *,
    primary_code: str,
    speed_trigger: bool,
    load_trigger: bool,
    temp_trigger: bool,
    voltage_trigger: bool,
) -> list[str]:
    reporting = _reporting_helpers()
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
    return reporting._dedupe_items(criteria)


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
    reporting = _reporting_helpers()
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
                "evidence": f"速度偏差 {reporting._format_float(speed_deviation)}%",
                "tasks": ["复核速度设定与反馈链路", "检查编码器信号与速度反馈一致性"],
            }
        )
    if load_trigger and max_load is not None:
        mappings.append(
            {
                "evidence": f"负载率 {reporting._format_float(max_load)}%",
                "tasks": ["检查负载波动、机械阻滞和制动状态"],
            }
        )
    if not temp_trigger and (max_motor_temp is not None or max_inverter_temp is not None):
        mappings.append(
            {
                "evidence": f"温度正常，电机最高 {reporting._format_float(max_motor_temp)}℃，变频器最高 {reporting._format_float(max_inverter_temp)}℃",
                "tasks": ["暂不生成温升排查任务"],
            }
        )
    if voltage_min is not None and voltage_max is not None:
        if voltage_trigger:
            tasks = ["检查供电与母线电压波动"]
            evidence = f"母线电压 {reporting._format_float(voltage_min)}-{reporting._format_float(voltage_max)}V 波动异常"
        else:
            tasks = ["暂不生成供电异常排查任务"]
            evidence = f"母线电压 {reporting._format_float(voltage_min)}-{reporting._format_float(voltage_max)}V 基本稳定"
        mappings.append({"evidence": evidence, "tasks": tasks})
    return mappings[:6]


def build_workorder_suggestion(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
) -> WorkOrderSuggestion:
    """Build a draft work-order suggestion from diagnosis artifacts."""

    reporting = _reporting_helpers()
    sql_report = reporting._build_sql_report_summary(sql_artifact, knowledge_artifact=knowledge_artifact)
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
    devices = reporting._unique_non_empty(sql_report.rows, "device_name")
    fault_codes = reporting._unique_codes(sql_report.rows, "fault_code")
    alarm_codes = reporting._unique_codes(sql_report.rows, "alarm_code")
    effective_codes = reporting._effective_codes(fault_codes, alarm_codes)
    primary_code = effective_codes[0] if effective_codes else ""
    primary_streak = reporting._latest_code_streak(sql_report.rows, primary_code) if primary_code else 0
    speed_deviation = reporting._speed_deviation_percent(latest)
    max_load = reporting._metric_max(sql_report.rows, "inverter_load_rate", "motor_load_rate")
    max_motor_temp = reporting._metric_max(sql_report.rows, "motor_temp")
    max_inverter_temp = reporting._metric_max(sql_report.rows, "inverter_temp", "inverter_radiator_temp")
    dc_voltage_values = reporting._metric_values(sql_report.rows, "dc_voltage")
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
        any(reporting._is_fault_code(code) for code in effective_codes)
        or primary_streak >= 3
        or (speed_deviation is not None and speed_deviation >= _SPEED_ERROR_CRITICAL_PERCENT)
        or (max_load is not None and max_load >= _LOAD_CRITICAL)
        or (max_motor_temp is not None and max_motor_temp >= _MOTOR_TEMP_CRITICAL)
        or (max_inverter_temp is not None and max_inverter_temp >= _INVERTER_TEMP_CRITICAL)
        or voltage_trigger
    )
    need_workorder = bool(severe_trigger or speed_trigger or load_trigger or temp_trigger)

    risk_level = "高" if (
        any(reporting._is_fault_code(code) for code in effective_codes)
        or (speed_deviation is not None and speed_deviation >= _SPEED_ERROR_CRITICAL_PERCENT)
        or (max_load is not None and max_load >= _LOAD_CRITICAL)
        or (max_motor_temp is not None and max_motor_temp >= _MOTOR_TEMP_CRITICAL)
        or (max_inverter_temp is not None and max_inverter_temp >= _INVERTER_TEMP_CRITICAL)
        or voltage_trigger
    ) else "中" if need_workorder else "低"

    if any(reporting._is_fault_code(code) for code in effective_codes) or primary_streak >= 3 or speed_trigger or load_trigger:
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
        diagnosis_clauses.append(f"速度偏差 { reporting._format_float(speed_deviation)}%")
    if load_trigger and max_load is not None:
        diagnosis_clauses.append(f"负载率 { reporting._format_float(max_load)}%")
    if temp_trigger:
        diagnosis_clauses.append(
            f"温度关注：电机 {reporting._format_float(max_motor_temp)}℃，变频器 {reporting._format_float(max_inverter_temp)}℃"
        )
    if voltage_trigger and voltage_min is not None and voltage_max is not None:
        diagnosis_clauses.append(f"母线电压 {reporting._format_float(voltage_min)}-{reporting._format_float(voltage_max)}V 波动异常")

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
        key_evidence.append(f"速度偏差 { reporting._format_float(speed_deviation)}%")
    if load_trigger and max_load is not None:
        key_evidence.append(f"负载率 { reporting._format_float(max_load)}%")
    if not temp_trigger and (max_motor_temp is not None or max_inverter_temp is not None):
        key_evidence.append(
            f"温度正常，电机最高 {reporting._format_float(max_motor_temp)}℃，变频器最高 {reporting._format_float(max_inverter_temp)}℃"
        )
    if voltage_min is not None and voltage_max is not None and not voltage_trigger:
        key_evidence.append(f"母线电压 {reporting._format_float(voltage_min)}-{reporting._format_float(voltage_max)}V")
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
        reason_parts.append(f"速度偏差 {reporting._format_float(speed_deviation)}% 超过关注阈值")
    if load_trigger and max_load is not None:
        reason_parts.append(f"负载率 {reporting._format_float(max_load)}% 进入关注区间")
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
        key_evidence=reporting._dedupe_items(key_evidence)[:5],
        processing_steps=processing_steps[:8],
        acceptance_criteria=acceptance_criteria[:6],
        task_mappings=task_mappings,
        equipment_object=equipment_object,
        fault_code=primary_code or None,
        title=title,
        trigger_source="故障诊断 Agent",
        status="待派单",
    )
