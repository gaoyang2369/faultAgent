"""Deterministic DCMA runtime diagnosis."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..contracts import AnalysisStepArtifact, DiagnosisRequest, KnowledgeStepArtifact, SqlStepArtifact
from ...single_agent.reporting.utils import (
    dedupe_items,
    format_float,
    format_value,
    metric_max,
    normalize_code,
    speed_deviation_percent,
    unique_codes,
    unique_non_empty,
)
from ...single_agent.sql_result_parser import parse_sql_rows
from ...single_agent.sql_safety import REAL_DATA_LATEST_TABLE
from .contracts import DiagnosticAssessment, RuleFinding, RuntimeMetricFeature, StructuredAnalysisArtifact
from .evidence_mapper import map_assessment_to_claims, map_assessment_to_evidence_items
from .thresholds import (
    DC_VOLTAGE_LOWER,
    DC_VOLTAGE_UPPER,
    FRESH_SECONDS,
    INVERTER_TEMPERATURE,
    LOAD_RATE,
    MOTOR_TEMPERATURE,
    RECENT_SECONDS,
    SPEED_DEVIATION,
)


def diagnose_dcma_runtime(
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    request: DiagnosisRequest,
    decision: Any | None = None,
) -> StructuredAnalysisArtifact:
    """Build a deterministic diagnosis for DCMA runtime data."""

    del decision
    rows = parse_sql_rows(sql_artifact.raw_output or sql_artifact.result_preview)
    if not sql_artifact.success or not rows:
        analysis = AnalysisStepArtifact(
            success=False,
            conclusion="SQL 未返回可解析 DCMA 运行数据，无法执行确定性运行诊断。",
            basis=[sql_artifact.summary],
            missing_information=["可解析的 DCMA 运行样本"],
            confidence="low",
            error=sql_artifact.error or "missing_runtime_rows",
        )
        assessment = DiagnosticAssessment(
            success=False,
            sample_count=0,
            conclusion=analysis.conclusion,
            missing_evidence=analysis.missing_information,
            confidence="low",
            metadata={"error": analysis.error},
        )
        return StructuredAnalysisArtifact(assessment=assessment, analysis_artifact=analysis)

    latest = rows[0]
    devices = unique_non_empty(rows, "device_name")
    asset = ", ".join(devices) or request.equipment_hint or "DCMA 系统"
    latest_time = format_value(latest.get("create_time"))
    oldest_time = format_value(rows[-1].get("create_time"))
    fault_codes = unique_codes(rows, "fault_code")
    alarm_codes = unique_codes(rows, "alarm_code")
    event_codes = dedupe_items([*fault_codes, *alarm_codes])
    currentness_level, currentness_warning = _currentness(latest_time)

    features = _build_features(rows, latest)
    findings = _build_findings(
        rows,
        event_codes,
        features,
        knowledge_artifact=knowledge_artifact,
        currentness_warning=currentness_warning,
    )
    knowledge_summaries = _knowledge_action_summaries(knowledge_artifact, event_codes)
    conclusion = _build_conclusion(
        asset=asset,
        rows=rows,
        latest=latest,
        event_codes=event_codes,
        currentness_warning=currentness_warning,
    )
    probable_causes = _build_probable_causes(features, event_codes, knowledge_artifact)
    recommendations = _build_recommendations(features, event_codes, knowledge_summaries)
    verification_items = _build_verification_items(features, event_codes)
    missing_evidence = _build_missing_evidence(event_codes, knowledge_artifact)
    confidence_details = _build_confidence_details(event_codes, knowledge_artifact, features, currentness_warning)
    confidence = "high" if knowledge_artifact.success and not currentness_warning else "medium"
    if not event_codes and currentness_warning:
        confidence = "low"

    basis = [
        f"SQL 返回 {len(rows)} 条 {REAL_DATA_LATEST_TABLE} 最近运行记录。",
        f"最新记录时间 {latest_time}，设备 {asset}，状态 {format_value(latest.get('status'))}。",
        f"事件码/告警码统计：{', '.join(event_codes) if event_codes else '未见有效事件码/告警码'}。",
        *[feature.summary for feature in features],
    ]
    if currentness_warning:
        basis.append(currentness_warning)
    basis.extend(f"RAG 处置要点：{item}" for item in knowledge_summaries[:3])

    assessment = DiagnosticAssessment(
        success=True,
        asset=asset,
        source_table=REAL_DATA_LATEST_TABLE,
        sample_count=len(rows),
        latest_sample_time=None if latest_time == "-" else latest_time,
        oldest_sample_time=None if oldest_time == "-" else oldest_time,
        currentness_level=currentness_level,
        currentness_warning=currentness_warning,
        event_codes=event_codes,
        features=features,
        findings=findings,
        conclusion=conclusion,
        probable_causes=probable_causes,
        verification_items=verification_items,
        recommendations=recommendations,
        risk_notice=_build_risk_notice(features, event_codes),
        missing_evidence=missing_evidence,
        confidence=confidence,
        confidence_details=confidence_details,
        metadata={"fault_codes": fault_codes, "alarm_codes": alarm_codes},
    )
    analysis = AnalysisStepArtifact(
        success=True,
        conclusion=assessment.conclusion,
        basis=dedupe_items(basis),
        probable_causes=assessment.probable_causes,
        verification_items=assessment.verification_items,
        recommendations=assessment.recommendations,
        risk_notice=assessment.risk_notice,
        missing_information=assessment.missing_evidence,
        confidence_details=assessment.confidence_details,
        confidence=assessment.confidence,
    )
    evidence_items = map_assessment_to_evidence_items(assessment, request=request)
    claims = map_assessment_to_claims(assessment, request=request)
    return StructuredAnalysisArtifact(
        assessment=assessment,
        analysis_artifact=analysis,
        evidence_items=evidence_items,
        claims=claims,
    )


def _build_features(rows: list[dict[str, Any]], latest: dict[str, Any]) -> list[RuntimeMetricFeature]:
    features: list[RuntimeMetricFeature] = []
    speed_deviation = speed_deviation_percent(latest)
    if speed_deviation is not None:
        status = _status_for_high_value(speed_deviation, SPEED_DEVIATION.warning, SPEED_DEVIATION.critical)
        features.append(
            RuntimeMetricFeature(
                feature_id="speed_deviation",
                metric_key=SPEED_DEVIATION.key,
                name="速度偏差率",
                value=speed_deviation,
                unit=SPEED_DEVIATION.unit,
                warning_threshold=SPEED_DEVIATION.warning,
                critical_threshold=SPEED_DEVIATION.critical,
                status=status,
                summary=(
                    f"最新速度偏差率 {format_float(speed_deviation)}%，"
                    f"{'进入关注阈值' if status != 'normal' else '未超过关注阈值'}。"
                ),
                evidence_id="ev_analysis_speed_deviation",
                metadata={"setpoint": latest.get("speed_setpoint"), "actual": latest.get("speed_actual")},
            )
        )

    max_load = metric_max(rows, "inverter_load_rate", "motor_load_rate")
    if max_load is not None:
        status = _status_for_high_value(max_load, LOAD_RATE.warning, LOAD_RATE.critical)
        features.append(
            RuntimeMetricFeature(
                feature_id="load_rate",
                metric_key=LOAD_RATE.key,
                name="负载率",
                value=round(max_load, 2),
                unit=LOAD_RATE.unit,
                window_max=round(max_load, 2),
                warning_threshold=LOAD_RATE.warning,
                critical_threshold=LOAD_RATE.critical,
                status=status,
                summary=(
                    f"样本窗口最高负载率 {format_float(max_load)}%，"
                    f"{'进入关注阈值' if status != 'normal' else '未超过关注阈值'}。"
                ),
                evidence_id="ev_analysis_load_rate",
                metadata={"columns": ["inverter_load_rate", "motor_load_rate"]},
            )
        )

    max_motor_temp = metric_max(rows, "motor_temp")
    max_inverter_temp = metric_max(rows, "inverter_temp", "inverter_radiator_temp")
    if max_motor_temp is not None or max_inverter_temp is not None:
        max_temp = max(value for value in [max_motor_temp, max_inverter_temp] if value is not None)
        warning = min(MOTOR_TEMPERATURE.warning, INVERTER_TEMPERATURE.warning)
        critical_values = [value for value in [MOTOR_TEMPERATURE.critical, INVERTER_TEMPERATURE.critical] if value]
        critical = min(critical_values) if critical_values else None
        status = _status_for_high_value(max_temp, warning, critical)
        features.append(
            RuntimeMetricFeature(
                feature_id="temperature",
                metric_key="temperature",
                name="温度",
                value=round(max_temp, 2),
                unit="℃",
                window_max=round(max_temp, 2),
                warning_threshold=warning,
                critical_threshold=critical,
                status=status,
                summary=(
                    f"样本窗口电机最高温度 {format_float(max_motor_temp)}℃，"
                    f"变频器/散热器最高温度 {format_float(max_inverter_temp)}℃，"
                    f"{'进入关注阈值' if status != 'normal' else '未超过温度阈值'}。"
                ),
                evidence_id="ev_analysis_temperature",
                metadata={"motor_temp_max": max_motor_temp, "inverter_temp_max": max_inverter_temp},
            )
        )

    dc_voltage = metric_max(rows, "dc_voltage")
    if dc_voltage is not None:
        status = "warning" if dc_voltage < DC_VOLTAGE_LOWER or dc_voltage > DC_VOLTAGE_UPPER else "normal"
        features.append(
            RuntimeMetricFeature(
                feature_id="dc_voltage",
                metric_key="dc_voltage",
                name="母线电压",
                value=round(dc_voltage, 2),
                unit="V",
                window_max=round(dc_voltage, 2),
                warning_threshold=DC_VOLTAGE_LOWER,
                critical_threshold=DC_VOLTAGE_UPPER,
                status=status,
                summary=(
                    f"样本窗口母线电压最高 {format_float(dc_voltage)}V，"
                    f"{'超出' if status != 'normal' else '位于'}参考范围 {format_float(DC_VOLTAGE_LOWER)}-{format_float(DC_VOLTAGE_UPPER)}V。"
                ),
                evidence_id="ev_analysis_dc_voltage",
                metadata={"reference_range": [DC_VOLTAGE_LOWER, DC_VOLTAGE_UPPER]},
            )
        )
    return features


def _build_findings(
    rows: list[dict[str, Any]],
    event_codes: list[str],
    features: list[RuntimeMetricFeature],
    *,
    knowledge_artifact: KnowledgeStepArtifact,
    currentness_warning: str | None,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    if event_codes:
        code_findings = []
        for code in event_codes:
            count = sum(
                1
                for row in rows
                if code in {normalize_code(row.get("fault_code")), normalize_code(row.get("alarm_code"))}
            )
            code_findings.append(f"{code} {count}/{len(rows)}")
        findings.append(
            RuleFinding(
                finding_id="event_persistence",
                rule_id="event_code_persistence",
                title="事件码持续出现",
                severity="warning",
                summary=f"样本窗口内事件码/告警码持续性：{'; '.join(code_findings)}。",
                supporting_feature_ids=[],
                supporting_evidence_ids=["ev_analysis_sample_currentness"],
                missing_evidence=[] if knowledge_artifact.success else ["知识库未命中明确事件码释义"],
                recommendation="确认事件码是否仍在当前实时状态中保持。",
            )
        )
    for feature in features:
        if feature.status not in {"warning", "critical"}:
            continue
        findings.append(
            RuleFinding(
                finding_id=feature.feature_id,
                rule_id=f"{feature.metric_key}_threshold",
                title=f"{feature.name}进入关注区间",
                severity="high" if feature.status == "critical" else "warning",
                summary=feature.summary,
                supporting_feature_ids=[feature.feature_id],
                supporting_evidence_ids=[feature.evidence_id],
                recommendation=_feature_recommendation(feature),
            )
        )
    if currentness_warning:
        findings.append(
            RuleFinding(
                finding_id="data_currentness",
                rule_id="data_currentness_warning",
                title="数据时效性不足",
                severity="notice",
                summary=currentness_warning,
                supporting_evidence_ids=["ev_analysis_sample_currentness"],
                recommendation="重新获取实时数据或确认采样链路后再判断当前风险。",
            )
        )
    return findings


def _build_conclusion(
    *,
    asset: str,
    rows: list[dict[str, Any]],
    latest: dict[str, Any],
    event_codes: list[str],
    currentness_warning: str | None,
) -> str:
    code_text = ", ".join(event_codes) if event_codes else "未见有效事件码/告警码"
    conclusion = (
        f"DCMA 确定性运行分析已处理 {len(rows)} 条 {REAL_DATA_LATEST_TABLE} 样本，"
        f"{asset} 最新记录状态为 {format_value(latest.get('status'))}，事件码/告警码为 {code_text}。"
    )
    if currentness_warning:
        conclusion += f" {currentness_warning}"
    return conclusion


def _build_probable_causes(
    features: list[RuntimeMetricFeature],
    event_codes: list[str],
    knowledge_artifact: KnowledgeStepArtifact,
) -> list[str]:
    items: list[str] = []
    if event_codes:
        if knowledge_artifact.success:
            items.append("事件码可能与知识库返回的参数、功能块或配置状态相关，需结合现场参数变更记录验证。")
        else:
            items.append("事件码持续出现，但缺少知识库释义，暂不能确认其机制原因。")
    if _feature_status(features, "speed_deviation") != "normal":
        items.append("速度给定与反馈偏差较大，可能关联运行使能、给定源、反馈链路或负载扰动。")
    if _feature_status(features, "load_rate") != "normal":
        items.append("负载率进入关注区间，可能关联机械传动、工艺负载、制动状态或参数限幅。")
    if _feature_status(features, "temperature") not in {"normal", "unknown"}:
        items.append("温度进入关注区间，可能关联散热条件、连续负载或环境温度。")
    return dedupe_items(items)


def _build_recommendations(
    features: list[RuntimeMetricFeature],
    event_codes: list[str],
    knowledge_summaries: list[str],
) -> list[str]:
    items: list[str] = []
    if event_codes:
        items.append("立即确认：确认最新采样是否对应当前设备状态，以及事件码是否仍在当前状态中保持。")
        for summary in knowledge_summaries[:2]:
            items.append(f"参数/配置检查：按知识库片段核对触发条件和处理项；{summary}")
        items.append("运行验证：记录复位、参数恢复或模式确认前后的状态字、控制字、运行命令和事件码变化。")
    if _feature_status(features, "speed_deviation") != "normal":
        items.append("关联排查：核对运行使能、速度给定来源、编码器/反馈链路和负载扰动。")
    if _feature_status(features, "load_rate") != "normal":
        items.append("关联排查：检查机械传动、工艺负载、制动状态和参数限幅设置。")
    if _feature_status(features, "temperature") not in {"normal", "unknown"}:
        items.append("关联排查：检查风道、散热器、柜内温度和连续运行负载。")
    items.append("闭环确认：复核状态字、控制字、母线电压、温度、负载率和功率指标是否与设备现象一致。")
    return dedupe_items(items)


def _build_verification_items(features: list[RuntimeMetricFeature], event_codes: list[str]) -> list[str]:
    items = []
    if event_codes:
        items.extend(
            [
                "当前设备是否仍保持该事件码/告警码。",
                "事件码出现前后的参数修改记录、单位设置变更记录和功能块激活时间点。",
                "复位或参数恢复前后的状态字、控制字、运行命令和事件码变化。",
            ]
        )
    if _feature_status(features, "speed_deviation") != "normal":
        items.append("速度给定来源、运行使能、编码器或反馈链路是否与实际转速一致。")
    if _feature_status(features, "load_rate") != "normal":
        items.append("机械传动、工艺负载、制动状态和限幅参数是否存在变化。")
    return dedupe_items(items)


def _build_missing_evidence(event_codes: list[str], knowledge_artifact: KnowledgeStepArtifact) -> list[str]:
    items = ["现场现象、复位结果、运行命令来源和参数变更记录"]
    if event_codes and not knowledge_artifact.success:
        items.insert(0, "知识库未命中异常码释义")
    return dedupe_items(items)


def _build_confidence_details(
    event_codes: list[str],
    knowledge_artifact: KnowledgeStepArtifact,
    features: list[RuntimeMetricFeature],
    currentness_warning: str | None,
) -> list[str]:
    items = []
    if event_codes:
        items.append("事件码识别：high，SQL 样本中存在有效事件码/告警码。")
        items.append("RAG 释义匹配：high，知识库已返回相关片段。" if knowledge_artifact.success else "RAG 释义匹配：low，知识库未命中明确片段。")
    if _feature_status(features, "speed_deviation") != "normal":
        items.append("速度偏差判断：medium，数据能证明偏差存在，但不能单独确认根因。")
    if _feature_status(features, "load_rate") != "normal":
        items.append("负载判断：medium，数据能证明负载进入关注区间，但需现场负载和机械检查闭环。")
    if currentness_warning:
        items.append("数据时效性：low，最新样本不代表当前实时状态。")
    return dedupe_items(items)


def _build_risk_notice(features: list[RuntimeMetricFeature], event_codes: list[str]) -> str:
    notices = []
    if event_codes:
        notices.append("事件码未闭环前，避免反复改参或复位，以免掩盖参数、功能块和运行模式证据。")
    if _feature_status(features, "speed_deviation") != "normal":
        notices.append("速度给定与反馈偏差较大，试运行前应确认运行命令、反馈链路和负载状态。")
    if _feature_status(features, "load_rate") != "normal":
        notices.append("负载率已进入关注区间，原因未确认前不宜扩大负载。")
    return " ".join(notices) or "当前未发现额外风险提示，仍需按现场安全规程执行复位和试运行。"


def _knowledge_action_summaries(knowledge_artifact: KnowledgeStepArtifact, event_codes: list[str]) -> list[str]:
    if not knowledge_artifact.success:
        return []
    blocks = knowledge_artifact.snippets or [
        item.strip() for item in knowledge_artifact.raw_output.split("\n\n") if item.strip()
    ]
    summaries = []
    for block in blocks:
        if event_codes and not any(code in block for code in event_codes):
            continue
        clean = re.sub(r"\s+", " ", block).strip()
        if clean:
            summaries.append(clean[:220])
    return dedupe_items(summaries)


def _feature_status(features: list[RuntimeMetricFeature], feature_id: str) -> str:
    matched = next((feature for feature in features if feature.feature_id == feature_id), None)
    return matched.status if matched is not None else "unknown"


def _feature_recommendation(feature: RuntimeMetricFeature) -> str:
    if feature.feature_id == "speed_deviation":
        return "核对运行使能、速度给定来源、反馈链路和负载扰动。"
    if feature.feature_id == "load_rate":
        return "检查机械传动、工艺负载、制动状态和参数限幅。"
    if feature.feature_id == "temperature":
        return "检查散热条件、风道、柜内温度和连续运行负载。"
    if feature.feature_id == "dc_voltage":
        return "复核供电质量、母线电压采样和变频器电源状态。"
    return "结合现场状态复核该指标。"


def _status_for_high_value(value: float, warning: float, critical: float | None) -> str:
    if critical is not None and value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "normal"


def _currentness(latest_time: str) -> tuple[str, str | None]:
    parsed = _parse_datetime(latest_time)
    if parsed is None:
        return "unknown", "无法解析最新样本时间，当前运行状态需重新确认。"
    delta_seconds = abs((datetime.now() - parsed).total_seconds())
    if delta_seconds <= FRESH_SECONDS:
        return "realtime", None
    if delta_seconds <= RECENT_SECONDS:
        return "recent", None
    hours = delta_seconds / 3600
    if hours < 48:
        age_text = f"{format_float(hours, 1)} 小时"
    else:
        age_text = f"{format_float(hours / 24, 1)} 天"
    return "stale", f"最新样本距当前时间约 {age_text}，数据已滞后，不代表当前实时状态。"


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
