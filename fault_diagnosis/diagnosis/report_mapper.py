"""统一把诊断产物映射为报告生成参数。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ..single_agent.reporting.operation import build_operation_diagnosis_report
from ..single_agent.sql_result_parser import parse_sql_rows


def _build_report_filename(prefix: str, thread_id: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}_{thread_id[-6:]}"


def _request_model(request: dict[str, Any], *, goal: str) -> DiagnosisRequest:
    return DiagnosisRequest(
        user_message=str(request.get("user_message") or request.get("message") or goal),
        user_identity=str(request.get("user_identity") or "历史线程"),
        equipment_hint=request.get("equipment_hint"),
        metric_hint=request.get("metric_hint"),
        fault_code_hint=request.get("fault_code_hint"),
        time_range_hint=request.get("time_range_hint") or "历史线程产物",
        needs_report=True,
        report_format="html",
        analysis_goal=str(request.get("analysis_goal") or goal),
    )


def _historical_data_quality(rows: list[dict[str, object]]) -> dict[str, object]:
    def row_time(row: dict[str, object]) -> str:
        value = row.get("create_time") or row.get("timestamp") or "-"
        return str(value or "-")

    return {
        "sample_count": len(rows),
        "oldest_sample_time": row_time(rows[-1]) if rows else "-",
        "latest_sample_time": row_time(rows[0]) if rows else "-",
        "freshness_seconds": 999999999,
        "freshness_label": "已滞后",
        "currentness": "本报告基于已保存的历史线程产物生成，不代表当前实时状态",
        "metric_availability": "未重新评估",
    }


def _status_summary(rows: list[dict[str, object]], request: DiagnosisRequest) -> dict[str, object]:
    return {
        "device": request.equipment_hint or "DCMA 系统",
        "initial_assessment": "基于历史线程产物生成报告，未重新查询实时数据库。",
    }


def _sql_model(sql_artifact: dict[str, Any]) -> SqlStepArtifact:
    return SqlStepArtifact(
        success=bool(sql_artifact.get("success", True)),
        summary=str(sql_artifact.get("summary") or "历史线程 SQL 结果"),
        sql_used=[str(item) for item in (sql_artifact.get("sql_used") or [])],
        result_preview=str(sql_artifact.get("result_preview") or ""),
        raw_output=str(sql_artifact.get("raw_output") or sql_artifact.get("result_preview") or ""),
        error=sql_artifact.get("error"),
    )


def _knowledge_model(knowledge_artifact: dict[str, Any]) -> KnowledgeStepArtifact:
    return KnowledgeStepArtifact(
        success=bool(knowledge_artifact.get("success", False)),
        query=str(knowledge_artifact.get("query") or ""),
        snippets=[str(item) for item in (knowledge_artifact.get("snippets") or [])],
        raw_output=str(knowledge_artifact.get("raw_output") or ""),
        error=knowledge_artifact.get("error"),
    )


def _analysis_model(analysis_artifact: dict[str, Any], fallback: str) -> AnalysisStepArtifact:
    return AnalysisStepArtifact(
        success=bool(analysis_artifact.get("success", True)),
        conclusion=str(analysis_artifact.get("conclusion") or fallback or "历史线程产物未提供明确结论。"),
        basis=[str(item) for item in (analysis_artifact.get("basis") or [])],
        probable_causes=[str(item) for item in (analysis_artifact.get("probable_causes") or [])],
        verification_items=[str(item) for item in (analysis_artifact.get("verification_items") or [])],
        recommendations=[str(item) for item in (analysis_artifact.get("recommendations") or [])],
        risk_notice=analysis_artifact.get("risk_notice"),
        missing_information=[str(item) for item in (analysis_artifact.get("missing_information") or [])],
        confidence_details=[str(item) for item in (analysis_artifact.get("confidence_details") or [])],
        confidence=str(analysis_artifact.get("confidence") or "medium"),
        error=analysis_artifact.get("error"),
    )


def _workorder_model(workorder_decision: dict[str, Any]) -> WorkOrderSuggestion | None:
    if not workorder_decision:
        return None
    try:
        return WorkOrderSuggestion(**workorder_decision)
    except Exception:
        return None


def _operation_payload(
    *,
    title: str,
    diagnosis_type: str,
    report_time: str,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion | None,
) -> str:
    rows = parse_sql_rows(sql_artifact.raw_output or sql_artifact.result_preview)
    report = build_operation_diagnosis_report(
        request=request,
        title=title,
        report_time=report_time,
        diagnosis_type=diagnosis_type,
        rows=rows,
        data_quality=_historical_data_quality(rows),
        status_summary=_status_summary(rows, request),
        sql_summary=sql_artifact.summary,
        sql_statement=";\n".join(sql_artifact.sql_used) or "无",
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
    )
    return json.dumps(report.model_dump(mode="json", exclude_none=True), ensure_ascii=False)


def map_artifact_to_report_payload(envelope: DiagnosisArtifactEnvelope) -> dict[str, Any]:
    """将结构化产物映射为 `save_report` 所需字段。"""

    artifact_type = str(envelope.workflow_type)
    payload = envelope.payload or {}
    request = payload.get("request") or {}
    report_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    if artifact_type == DiagnosisArtifactType.FAULT_DIAGNOSIS.value:
        sql_artifact = payload.get("sql_artifact") or {}
        knowledge_artifact = payload.get("knowledge_artifact") or {}
        analysis_artifact = payload.get("analysis_artifact") or {}
        workorder_decision = payload.get("workorder_decision") or {}
        report_filename = _build_report_filename("dcma_report_generation_fault", envelope.thread_id)
        request_model = _request_model(request, goal="历史故障诊断报告")
        sql_model = _sql_model(sql_artifact)
        knowledge_model = _knowledge_model(knowledge_artifact)
        analysis_model = _analysis_model(analysis_artifact, envelope.final_answer)
        return {
            "title": "DCMA 故障诊断报告",
            "report_filename": report_filename,
            "chart_payload": "",
            "operation_report_payload": _operation_payload(
                title="DCMA 故障诊断报告",
                diagnosis_type=request.get("fault_code_hint") or "故障诊断",
                report_time=report_time,
                request=request_model,
                sql_artifact=sql_model,
                knowledge_artifact=knowledge_model,
                analysis_artifact=analysis_model,
                workorder_suggestion=_workorder_model(workorder_decision),
            ),
        }

    if artifact_type == DiagnosisArtifactType.STATUS_INSPECTION.value:
        sql_artifact = payload.get("sql_artifact") or {}
        knowledge_artifact = payload.get("knowledge_artifact") or {}
        inspection_artifact = payload.get("inspection_artifact") or {}
        report_filename = _build_report_filename("dcma_report_generation_inspection", envelope.thread_id)
        request_model = _request_model(request, goal="历史运行诊断报告")
        sql_model = _sql_model(sql_artifact)
        knowledge_model = _knowledge_model(knowledge_artifact)
        analysis_model = _analysis_model(
            {
                "conclusion": inspection_artifact.get("summary") or envelope.final_answer,
                "basis": inspection_artifact.get("observed_metrics") or [],
                "recommendations": inspection_artifact.get("suggested_actions") or [],
                "confidence": "medium",
            },
            envelope.final_answer,
        )
        return {
            "title": "DCMA 运行诊断报告",
            "report_filename": report_filename,
            "chart_payload": "",
            "operation_report_payload": _operation_payload(
                title="DCMA 运行诊断报告",
                diagnosis_type="运行诊断",
                report_time=report_time,
                request=request_model,
                sql_artifact=sql_model,
                knowledge_artifact=knowledge_model,
                analysis_artifact=analysis_model,
                workorder_suggestion=None,
            ),
        }

    raise ValueError(f"当前诊断产物类型不支持独立生成报告：{artifact_type}")
