"""Evidence bundle construction for the restricted single-agent runtime."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    Claim,
    ClaimConfidence,
    DiagnosisRequest,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ..diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text
from .contracts import SingleAgentDecision
from .serialization import preview, sanitize_for_json, stringify
from .sql_result_parser import parse_sql_rows
from .sql_safety import REAL_DATA_LATEST_TABLE

WORKFLOW_ID = "WF_FAULT_DIAGNOSIS_V1"
WORKFLOW_VERSION = "1.0.0"

_EMPTY_CODE_VALUES = {"", "0", "0.0", "none", "null", "无", "正常", "nan"}
_KNOWLEDGE_SOURCE_RE = re.compile(r"^(来源|来源文件|file_id|source_type|extract_backend|来源页码|检索方式)[：:]\s*(.*)$")
_FRESH_SECONDS = 5 * 60
_RECENT_SECONDS = 60 * 60
_SPEED_WARNING_PERCENT = 20.0
_LOAD_WARNING = 75.0
_MOTOR_TEMP_WARNING = 70.0
_INVERTER_TEMP_WARNING = 65.0


def initialize_evidence_bundle(
    *,
    trace_id: str,
    request: DiagnosisRequest,
    decision: SingleAgentDecision,
) -> EvidenceBundle:
    """Create the request-scoped empty evidence ledger."""

    task = {
        "task_type": "fault_diagnosis",
        "workflow_id": WORKFLOW_ID,
        "workflow_version": WORKFLOW_VERSION,
        "user_query": request.user_message,
        "user_identity": request.user_identity,
        "asset_id": request.equipment_hint,
        "symptom": request.metric_hint or request.fault_code_hint or request.analysis_goal,
        "time_range_hint": request.time_range_hint,
        "requires_sql": decision.needs_sql,
        "requires_knowledge": decision.needs_knowledge,
        "requires_report": decision.needs_report,
    }
    return EvidenceBundle(
        bundle_id=_bundle_id(trace_id),
        trace_id=trace_id,
        task={key: value for key, value in task.items() if value not in (None, "", [])},
        artifacts={"workflow_id": WORKFLOW_ID, "workflow_version": WORKFLOW_VERSION},
    )


def build_evidence_bundle(
    *,
    trace_id: str,
    request: DiagnosisRequest,
    decision: SingleAgentDecision,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion,
    report_artifact: ReportStepArtifact,
) -> EvidenceBundle:
    """Build and validate a complete evidence bundle for one run."""

    bundle = initialize_evidence_bundle(trace_id=trace_id, request=request, decision=decision)
    evidence_items = _dedupe_evidence(
        [
            _user_request_evidence(request),
            *_sql_evidence_items(sql_artifact, request=request),
            *_knowledge_evidence_items(knowledge_artifact, request=request),
        ]
    )
    evidence_ids = [item.evidence_id for item in evidence_items if item.evidence_id]
    claims = _build_claims(
        request=request,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
        evidence_ids=evidence_ids,
    )
    final_claim_ids = [claim.claim_id for claim in claims if claim.status in {"candidate", "confirmed", "final"}]
    bundle.evidence_items = evidence_items
    bundle.claims = claims
    bundle.final_claim_ids = final_claim_ids
    bundle.artifacts.update(
        {
            "sql_success": sql_artifact.success,
            "knowledge_success": knowledge_artifact.success,
            "report_filename": report_artifact.report_filename,
            "report_success": report_artifact.success,
        }
    )
    bundle.quality_checks = validate_evidence_bundle(bundle)
    return bundle


def validate_evidence_bundle(bundle: EvidenceBundle) -> dict[str, Any]:
    """Return deterministic evidence-chain quality checks."""

    evidence_ids = {item.evidence_id for item in bundle.evidence_items if item.evidence_id}
    claim_refs = [
        evidence_id
        for claim in bundle.claims
        for evidence_id in [*claim.supporting_evidence_ids, *claim.contradicting_evidence_ids]
    ]
    dangling_refs = sorted({evidence_id for evidence_id in claim_refs if evidence_id not in evidence_ids})
    missing_evidence_items = [
        item
        for claim in bundle.claims
        for item in claim.missing_evidence
        if str(item or "").strip()
    ]
    evidence_types = {item.evidence_type for item in bundle.evidence_items}
    source_types = {item.source_type for item in bundle.evidence_items}
    return {
        "has_asset": bool(bundle.task.get("asset_id") or _first_asset_id(bundle.evidence_items)),
        "has_user_request": any(item.source_type == "user" for item in bundle.evidence_items),
        "has_current_status": any(item.evidence_type in {"device_status", "metric_snapshot"} for item in bundle.evidence_items),
        "has_alarm_history": "alarm_event" in evidence_types,
        "has_manual_reference": "knowledge_base" in source_types,
        "has_timeseries_feature": "timeseries_feature" in evidence_types,
        "all_claims_have_evidence": bool(bundle.claims) and all(claim.supporting_evidence_ids for claim in bundle.claims),
        "no_dangling_evidence_refs": not dangling_refs,
        "dangling_evidence_refs": dangling_refs,
        "missing_evidence_disclosed": bool(missing_evidence_items) or all(not claim.missing_evidence for claim in bundle.claims),
        "evidence_count": len(bundle.evidence_items),
        "claim_count": len(bundle.claims),
    }


def build_output_guardrail_result(final_answer: str, bundle: EvidenceBundle | None) -> dict[str, Any]:
    """Build a lightweight output guardrail result for trace and artifact metadata."""

    warnings: list[str] = []
    if not final_answer.strip():
        warnings.append("final_answer_empty")
    quality_checks = bundle.quality_checks if bundle is not None else {}
    if quality_checks and not quality_checks.get("no_dangling_evidence_refs", True):
        warnings.append("dangling_evidence_refs")
    if quality_checks and not quality_checks.get("all_claims_have_evidence", True):
        warnings.append("claim_without_supporting_evidence")
    return {
        "passed": not warnings,
        "warnings": warnings,
        "bundle_id": bundle.bundle_id if bundle is not None else None,
        "evidence_count": len(bundle.evidence_items) if bundle is not None else 0,
        "claim_count": len(bundle.claims) if bundle is not None else 0,
    }


def build_tool_evidence_preview(*, tool_name: str, output: Any) -> list[dict[str, Any]]:
    """Build compact evidence summaries for tool_end SSE events."""

    if tool_name == "sql_db_query":
        artifact = SqlStepArtifact(
            success=True,
            summary="SQL 工具返回运行数据",
            raw_output=stringify(output),
            result_preview=preview(output),
        )
        return [_tool_evidence_payload(item) for item in _sql_evidence_items(artifact, request=None)]
    if tool_name == "query_knowledge_base":
        raw_output = stringify(output)
        artifact = KnowledgeStepArtifact(
            success=bool(raw_output.strip()),
            query="",
            snippets=[item.strip() for item in raw_output.split("\n\n") if item.strip()][:3],
            raw_output=raw_output,
            error=None if raw_output.strip() else "知识库未返回内容",
        )
        return [_tool_evidence_payload(item) for item in _knowledge_evidence_items(artifact, request=None)]
    return []


def _bundle_id(trace_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_]+", "_", trace_id or "unknown").strip("_")
    return f"bundle_{suffix or 'unknown'}"


def _user_request_evidence(request: DiagnosisRequest) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="ev_user_request",
        evidence_type="user_statement",
        source_type="user",
        source_name="chat_message",
        asset_id=request.equipment_hint,
        content={
            "user_message": request.user_message,
            "analysis_goal": request.analysis_goal,
            "equipment_hint": request.equipment_hint,
            "metric_hint": request.metric_hint,
            "fault_code_hint": request.fault_code_hint,
            "time_range_hint": request.time_range_hint,
        },
        summary=f"用户请求：{request.analysis_goal or request.user_message}",
        quality=EvidenceQuality(reliability="medium", freshness="current", relevance="high", completeness="partial"),
        metadata={"user_identity": request.user_identity},
        title="用户请求",
        importance="medium",
    )


def _sql_evidence_items(sql_artifact: SqlStepArtifact, *, request: DiagnosisRequest | None) -> list[EvidenceItem]:
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
    asset_id = _first_non_empty([request.equipment_hint if request else None, *devices])
    latest_time = _format_value(latest.get("create_time"))
    oldest_time = _format_value(rows[-1].get("create_time"))
    fault_codes = _unique_codes(rows, "fault_code")
    alarm_codes = _unique_codes(rows, "alarm_code")
    effective_codes = _dedupe([*fault_codes, *alarm_codes])
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

    metric_items = _sql_metric_evidence(rows, asset_id=asset_id, latest_time=latest_time)
    items.extend(metric_items)
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


def _knowledge_evidence_items(
    knowledge_artifact: KnowledgeStepArtifact,
    *,
    request: DiagnosisRequest | None,
) -> list[EvidenceItem]:
    raw_output = (knowledge_artifact.raw_output or "").strip()
    if not knowledge_artifact.success or not raw_output:
        summary = knowledge_artifact.error or "本次请求未获得可用知识库证据。"
        return [
            EvidenceItem(
                evidence_id="ev_kb_result_missing",
                evidence_type="tool_error",
                source_type="knowledge_base",
                source_name="knowledge_base",
                asset_id=request.equipment_hint if request else None,
                content={"query": knowledge_artifact.query, "error": knowledge_artifact.error, "raw_output": raw_output},
                summary=summary,
                quality=EvidenceQuality(reliability="medium", freshness="unknown", relevance="medium", completeness="missing"),
                metadata={"query": knowledge_artifact.query},
                title="知识库检索结果",
                importance="low",
            )
        ]

    items: list[EvidenceItem] = []
    blocks = [block.strip() for block in raw_output.split("\n\n") if block.strip()][:3]
    for index, block in enumerate(blocks, start=1):
        metadata = _knowledge_metadata(block)
        codes = extract_fault_codes_from_text(block)
        evidence_type = "fault_code_reference" if codes else "manual_reference"
        item_id = f"ev_kb_{index:03d}"
        summary = _knowledge_summary(block, codes)
        items.append(
            EvidenceItem(
                evidence_id=item_id,
                evidence_type=evidence_type,
                source_type="knowledge_base",
                source_name=str(metadata.get("来源文件") or metadata.get("来源") or "knowledge_base"),
                asset_id=request.equipment_hint if request else None,
                content={"query": knowledge_artifact.query, "codes": codes, "snippet": block[:1200]},
                summary=summary,
                quality=EvidenceQuality(reliability="high", freshness="unknown", relevance="high", completeness="partial"),
                metadata={"query": knowledge_artifact.query, **metadata},
                title="知识库手册片段",
                importance="high" if codes else "medium",
            )
        )
    return items


def _build_claims(
    *,
    request: DiagnosisRequest,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion,
    evidence_ids: list[str],
) -> list[Claim]:
    if not evidence_ids:
        return []

    claims: list[Claim] = []
    sql_ids = [item for item in evidence_ids if item.startswith("ev_sql_")]
    kb_ids = [item for item in evidence_ids if item.startswith("ev_kb_") and item != "ev_kb_result_missing"]
    support_all = _prefer_supporting_ids(evidence_ids)
    missing_evidence = _dedupe(analysis_artifact.missing_information)
    confidence = _confidence_from_analysis(analysis_artifact)

    if analysis_artifact.conclusion:
        claims.append(
            Claim(
                claim_id="claim_diagnosis_summary",
                claim_type="diagnosis_summary",
                asset_id=request.equipment_hint,
                statement=analysis_artifact.conclusion,
                confidence=confidence,
                supporting_evidence_ids=support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="综合用户请求、SQL 运行数据、知识库片段和规则分析形成诊断摘要。",
                status="final",
                created_by="analysis_node",
            )
        )

    for index, cause in enumerate(_dedupe(analysis_artifact.probable_causes)[:3], start=1):
        claims.append(
            Claim(
                claim_id=f"claim_root_cause_{index:03d}",
                claim_type="root_cause_candidate",
                asset_id=request.equipment_hint,
                statement=cause,
                confidence=confidence,
                supporting_evidence_ids=_dedupe([*sql_ids, *kb_ids]) or support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="该根因候选由运行数据特征和可用知识库提示共同支撑，仍需现场闭环验证。",
                status="candidate",
                created_by="analysis_node",
            )
        )

    if analysis_artifact.risk_notice:
        claims.append(
            Claim(
                claim_id="claim_risk_assessment",
                claim_type="risk_assessment",
                asset_id=request.equipment_hint,
                statement=analysis_artifact.risk_notice,
                confidence=ClaimConfidence(level="medium", score=0.68, reason="风险提示通常依赖数据异常和现场安全规程，需现场确认。"),
                supporting_evidence_ids=support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="根据当前异常状态和处置闭环要求形成风险提示。",
                status="candidate",
                created_by="analysis_node",
            )
        )

    if analysis_artifact.recommendations:
        claims.append(
            Claim(
                claim_id="claim_recommendation",
                claim_type="recommendation",
                asset_id=request.equipment_hint,
                statement="；".join(_dedupe(analysis_artifact.recommendations)[:3]),
                confidence=ClaimConfidence(level=analysis_artifact.confidence if analysis_artifact.confidence in {"high", "medium", "low"} else "medium", score=None, reason="建议基于诊断结论、关键依据和风险提示生成。"),
                supporting_evidence_ids=support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="将诊断结论转化为现场可执行的下一步建议。",
                status="candidate",
                created_by="analysis_node",
            )
        )

    claims.append(
        Claim(
            claim_id="claim_workorder_decision",
            claim_type="workorder_decision",
            asset_id=request.equipment_hint or workorder_suggestion.equipment_object or None,
            statement=workorder_suggestion.reason or ("建议生成维修工单" if workorder_suggestion.need_workorder else "暂不建议自动生成维修工单"),
            confidence=ClaimConfidence(
                level="medium" if workorder_suggestion.need_workorder else "low",
                score=0.72 if workorder_suggestion.need_workorder else 0.45,
                reason="工单建议由规则阈值、异常持续性和分析结论共同生成。",
            ),
            supporting_evidence_ids=support_all,
            missing_evidence=missing_evidence,
            reasoning_summary=workorder_suggestion.diagnosis_conclusion or workorder_suggestion.reason,
            status="candidate",
            created_by="workorder_decision_node",
            decision="suggest_create" if workorder_suggestion.need_workorder else "skip_create",
            reason_codes=_workorder_reason_codes(workorder_suggestion),
        )
    )
    return claims


def _confidence_from_analysis(analysis_artifact: AnalysisStepArtifact) -> ClaimConfidence:
    level = analysis_artifact.confidence if analysis_artifact.confidence in {"high", "medium", "low"} else "medium"
    score = {"high": 0.85, "medium": 0.65, "low": 0.4}[level]
    reason = "；".join(analysis_artifact.confidence_details[:3]) if analysis_artifact.confidence_details else ""
    return ClaimConfidence(level=level, score=score, reason=reason)


def _prefer_supporting_ids(evidence_ids: list[str]) -> list[str]:
    preferred = [
        evidence_id
        for evidence_id in evidence_ids
        if not evidence_id.endswith("_missing") and not evidence_id.startswith("ev_user_")
    ]
    return preferred or evidence_ids[:]


def _workorder_reason_codes(workorder_suggestion: WorkOrderSuggestion) -> list[str]:
    codes: list[str] = []
    text = " ".join([workorder_suggestion.reason, *workorder_suggestion.key_evidence])
    if "速度偏差" in text:
        codes.append("speed_deviation_above_threshold")
    if "负载率" in text:
        codes.append("load_rate_attention")
    if "温度" in text:
        codes.append("temperature_attention")
    if workorder_suggestion.fault_code:
        codes.append("fault_or_alarm_code_present")
    if workorder_suggestion.need_workorder:
        codes.append("workorder_rule_triggered")
    return codes


def _tool_evidence_payload(item: EvidenceItem) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "evidence_type": item.evidence_type,
        "source_type": item.source_type,
        "summary": item.summary,
        "quality": item.quality.model_dump(),
    }


def _dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    deduped: list[EvidenceItem] = []
    seen: set[str] = set()
    for item in items:
        key = item.evidence_id or item.summary
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _knowledge_metadata(block: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in block.splitlines():
        matched = _KNOWLEDGE_SOURCE_RE.match(line.strip())
        if matched:
            metadata[matched.group(1)] = matched.group(2).strip()
    return metadata


def _knowledge_summary(block: str, codes: list[str]) -> str:
    lines = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or _KNOWLEDGE_SOURCE_RE.match(line):
            continue
        lines.append(line)
        if len("；".join(lines)) > 220:
            break
    prefix = f"{', '.join(codes)}：" if codes else ""
    body = "；".join(lines) or block.strip()
    return f"{prefix}{body[:260].strip()}"


def _unique_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    return _dedupe([_format_value(row.get(key)) for row in rows if _format_value(row.get(key)) != "-"])


def _unique_codes(rows: list[dict[str, Any]], key: str) -> list[str]:
    return _dedupe([_normalize_code(row.get(key)) for row in rows if _normalize_code(row.get(key))])


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in _EMPTY_CODE_VALUES else text


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"


def _format_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return _format_value(value)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    chronological_rows = list(reversed(rows))
    return [value for row in chronological_rows if (value := _to_float(row.get(key))) is not None]


def _metric_max(rows: list[dict[str, Any]], *keys: str) -> float | None:
    values = [value for key in keys for value in _metric_values(rows, key)]
    return max(values) if values else None


def _speed_deviation_percent(latest: dict[str, Any]) -> float | None:
    setpoint = _to_float(latest.get("speed_setpoint"))
    actual = _to_float(latest.get("speed_actual"))
    if setpoint is None or actual is None or abs(setpoint) < 1:
        return None
    return round(abs(actual - setpoint) / max(abs(setpoint), 1) * 100, 2)


def _is_abnormal_row(row: dict[str, Any]) -> bool:
    return bool(_normalize_code(row.get("fault_code")) or _normalize_code(row.get("alarm_code")))


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


def _first_non_empty(values: list[Any]) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text and text != "-":
            return text
    return None


def _first_asset_id(items: list[EvidenceItem]) -> str | None:
    return _first_non_empty([item.asset_id for item in items])


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item or "").strip() for item in items if str(item or "").strip()))


def dump_evidence_bundle(bundle: EvidenceBundle | None) -> dict[str, Any] | None:
    """Serialize bundle with sanitization for trace metadata."""

    if bundle is None:
        return None
    return sanitize_for_json(bundle.model_dump(exclude_none=True))
