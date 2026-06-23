"""统一把诊断产物映射为报告生成参数。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from ..single_agent.report_sections import build_workorder_todo_markdown


def _build_report_filename(prefix: str, thread_id: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}_{thread_id[-6:]}"


def _workorder_section(workorder_decision: dict[str, Any]) -> str:
    if not workorder_decision or not workorder_decision.get("need_workorder"):
        return ""
    section = build_workorder_todo_markdown(
        title=workorder_decision.get("title"),
        workorder_type=workorder_decision.get("workorder_type"),
        risk_level=workorder_decision.get("risk_level"),
        priority=workorder_decision.get("priority"),
        priority_label=workorder_decision.get("priority_label"),
        assignee_role=workorder_decision.get("assignee_role"),
        suggested_completion_window=workorder_decision.get("suggested_completion_window"),
        key_evidence=workorder_decision.get("key_evidence") or [],
        processing_steps=workorder_decision.get("processing_steps") or [],
        acceptance_criteria=workorder_decision.get("acceptance_criteria") or [],
    )
    return f"\n\n{section}"


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
        repair_recommendations = "\n".join(
            f"- {item}" for item in (analysis_artifact.get("recommendations") or [])
        ) or "- 暂无具体处置建议"
        sql_statement_text = ";\n".join(sql_artifact.get("sql_used") or []) or "无"
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
            "repair_recommendations": f"{repair_recommendations}{_workorder_section(workorder_decision)}",
            "preventive_maintenance": (
                "### 风险与边界说明\n"
                "本报告基于当前线程已保存的结构化诊断产物生成，未重新查询实时数据库。"
                "若现场状态、告警状态或维修记录已经变化，根因判断和处置优先级需人工复核。\n\n"
                "建议结合本次诊断结果持续跟踪关键指标，并复核相关部件状态。"
            ),
            "diagnosis_basis": (
                "### 请求摘要\n"
                f"- {envelope.request_summary or '无'}\n\n"
                "### SQL 摘要\n"
                f"- {sql_artifact.get('summary') or '无'}\n\n"
                "### SQL 语句\n"
                f"```sql\n{sql_statement_text}\n```\n\n"
                "### 知识与分析依据\n"
                f"- 知识查询：{knowledge_artifact.get('query') or '无'}\n"
                f"- 分析依据：{'; '.join(analysis_artifact.get('basis') or []) or '无'}"
            ),
            "report_filename": report_filename,
        }

    if artifact_type == DiagnosisArtifactType.STATUS_INSPECTION.value:
        sql_artifact = payload.get("sql_artifact") or {}
        knowledge_artifact = payload.get("knowledge_artifact") or {}
        inspection_artifact = payload.get("inspection_artifact") or {}
        report_filename = _build_report_filename("dcma_report_generation_inspection", envelope.thread_id)
        sql_statement_text = ";\n".join(sql_artifact.get("sql_used") or []) or "无"
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
            "preventive_maintenance": (
                "### 风险与边界说明\n"
                "本报告基于当前线程已保存的状态巡检产物生成，未重新查询实时数据库。"
                "若现场状态或数据窗口已变化，需要重新巡检后再定论。\n\n"
                "建议根据巡检风险等级持续关注关键指标趋势，必要时安排复检。"
            ),
            "diagnosis_basis": (
                "### 请求摘要\n"
                f"- {envelope.request_summary or '无'}\n\n"
                "### SQL 摘要\n"
                f"- {sql_artifact.get('summary') or '无'}\n\n"
                "### SQL 语句\n"
                f"```sql\n{sql_statement_text}\n```\n\n"
                "### 巡检依据\n"
                f"- 风险等级：{inspection_artifact.get('risk_level') or 'low'}\n"
                f"- 观察指标：{'; '.join(inspection_artifact.get('observed_metrics') or []) or '无'}"
            ),
            "report_filename": report_filename,
        }

    raise ValueError(f"当前诊断产物类型不支持独立生成报告：{artifact_type}")
