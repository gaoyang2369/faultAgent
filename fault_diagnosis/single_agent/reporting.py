"""Report payload and final-answer formatting helpers."""

from __future__ import annotations

import re

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
)

_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9._\-]+\.(?:md|html))", re.IGNORECASE)


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
    return {
        "title": "DCMA 故障诊断报告",
        "report_time": current_time,
        "diagnosis_object": request.equipment_hint or "DCMA 系统",
        "diagnosis_type": request.fault_code_hint or "故障诊断",
        "executive_summary": analysis_artifact.conclusion,
        "diagnosis_overview": "本报告由限制型单 Agent 生成，流程包含请求理解、受限 SQL、知识检索、诊断分析和报告保存。",
        "diagnosis_details": (
            f"【SQL 结果摘要】\n{sql_artifact.result_preview or sql_artifact.raw_output or '无'}\n\n"
            f"【知识检索摘要】\n{knowledge_artifact.raw_output or '无'}"
        ),
        "fault_inference": analysis_artifact.conclusion,
        "repair_recommendations": "\n".join(f"- {item}" for item in analysis_artifact.recommendations)
        or "- 暂无具体处置建议",
        "preventive_maintenance": "建议结合本次诊断结果持续跟踪关键指标，并复核相关部件状态。",
        "diagnosis_basis": (
            f"SQL 摘要：{sql_artifact.summary}\n"
            f"SQL 语句：{'; '.join(sql_artifact.sql_used) or '无'}\n"
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
