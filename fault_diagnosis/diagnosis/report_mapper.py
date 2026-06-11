"""统一把诊断产物映射为报告生成参数。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType


def _build_report_filename(prefix: str, thread_id: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}_{thread_id[-6:]}"


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
        report_filename = _build_report_filename("dcma_report_generation_fault", envelope.thread_id)
        return {
            "title": "DCMA 故障诊断报告",
            "report_time": report_time,
            "diagnosis_object": request.get("equipment_hint") or "DCMA 系统",
            "diagnosis_type": request.get("fault_code_hint") or "故障诊断",
            "executive_summary": analysis_artifact.get("conclusion") or envelope.final_answer,
            "diagnosis_overview": "本报告基于当前线程最近一次故障诊断结果生成，无需重新执行 SQL 查询和分析。",
            "diagnosis_details": (
                f"【SQL 结果摘要】\n{sql_artifact.get('result_preview') or sql_artifact.get('raw_output') or '无'}\n\n"
                f"【知识检索摘要】\n{knowledge_artifact.get('raw_output') or '无'}"
            ),
            "fault_inference": analysis_artifact.get("conclusion") or envelope.final_answer,
            "repair_recommendations": "\n".join(
                f"- {item}" for item in (analysis_artifact.get("recommendations") or [])
            ) or "- 暂无具体处置建议",
            "preventive_maintenance": "建议结合本次诊断结果持续跟踪关键指标，并复核相关部件状态。",
            "diagnosis_basis": (
                f"请求摘要：{envelope.request_summary}\n"
                f"SQL 摘要：{sql_artifact.get('summary') or '无'}\n"
                f"SQL 语句：{'; '.join(sql_artifact.get('sql_used') or []) or '无'}\n"
                f"知识查询：{knowledge_artifact.get('query') or '无'}\n"
                f"分析依据：{'; '.join(analysis_artifact.get('basis') or []) or '无'}"
            ),
            "report_filename": report_filename,
        }

    if artifact_type == DiagnosisArtifactType.STATUS_INSPECTION.value:
        sql_artifact = payload.get("sql_artifact") or {}
        knowledge_artifact = payload.get("knowledge_artifact") or {}
        inspection_artifact = payload.get("inspection_artifact") or {}
        report_filename = _build_report_filename("dcma_report_generation_inspection", envelope.thread_id)
        return {
            "title": "DCMA 运行诊断报告",
            "report_time": report_time,
            "diagnosis_object": request.get("equipment_hint") or "DCMA 系统",
            "diagnosis_type": "运行诊断",
            "executive_summary": inspection_artifact.get("summary") or envelope.final_answer,
            "diagnosis_overview": "本报告基于当前线程最近一次状态巡检结果生成，无需重新执行 SQL 查询和巡检分析。",
            "diagnosis_details": (
                f"【巡检 SQL 摘要】\n{sql_artifact.get('result_preview') or sql_artifact.get('raw_output') or '无'}\n\n"
                f"【观察指标】\n{'; '.join(inspection_artifact.get('observed_metrics') or []) or '无'}\n\n"
                f"【发现异常】\n{'; '.join(inspection_artifact.get('detected_anomalies') or []) or '无'}\n\n"
                f"【知识补充】\n{knowledge_artifact.get('raw_output') or '无'}"
            ),
            "fault_inference": inspection_artifact.get("summary") or envelope.final_answer,
            "repair_recommendations": "\n".join(
                f"- {item}" for item in (inspection_artifact.get("suggested_actions") or [])
            ) or "- 暂无具体建议动作",
            "preventive_maintenance": "建议根据巡检风险等级持续关注关键指标趋势，必要时安排复检。",
            "diagnosis_basis": (
                f"请求摘要：{envelope.request_summary}\n"
                f"SQL 摘要：{sql_artifact.get('summary') or '无'}\n"
                f"SQL 语句：{'; '.join(sql_artifact.get('sql_used') or []) or '无'}\n"
                f"风险等级：{inspection_artifact.get('risk_level') or 'low'}\n"
                f"观察指标：{'; '.join(inspection_artifact.get('observed_metrics') or []) or '无'}"
            ),
            "report_filename": report_filename,
        }

    raise ValueError(f"当前诊断产物类型不支持独立生成报告：{artifact_type}")
