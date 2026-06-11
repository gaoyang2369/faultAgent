"""Rule-based request understanding fallback and capability decisions."""

from __future__ import annotations

import re
from typing import Any

from ..diagnosis.contracts import DiagnosisRequest
from .contracts import SingleAgentDecision

_FAULT_CODE_RE = re.compile(r"\b([A-Z]\d{4,})\b", re.IGNORECASE)
_DEVICE_RE = re.compile(r"\b([A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机)\b", re.IGNORECASE)

REPORT_KEYWORDS = ("报告", "出报告", "生成报告", "导出报告", "整理成报告", "形成报告")
REPORT_CONTEXT_HINTS = ("刚才", "刚刚", "上一轮", "上一条", "上一次", "前面的结果", "诊断结果", "巡检结果")
SQL_KEYWORDS = (
    "设备",
    "机台",
    "产线",
    "故障",
    "报警",
    "告警",
    "异常",
    "状态",
    "当前",
    "最近",
    "历史",
    "数据",
    "趋势",
    "温度",
    "振动",
    "电流",
    "转速",
    "负载",
)
KNOWLEDGE_KEYWORDS = (
    "故障码",
    "原因",
    "根因",
    "怎么处理",
    "如何处理",
    "处置",
    "维修",
    "排查",
    "手册",
    "说明",
    "步骤",
    "含义",
    "是什么意思",
)


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def looks_like_report_handoff(message: str) -> bool:
    normalized = (message or "").strip()
    if not has_any(normalized, REPORT_KEYWORDS):
        return False
    if has_any(normalized, REPORT_CONTEXT_HINTS):
        return True
    compact = normalized.replace(" ", "")
    return compact in {"报告", "出报告", "生成报告", "导出报告", "整理成报告"}


def fallback_understanding_payload(message: str, user_identity: str) -> dict[str, Any]:
    fault_code_match = _FAULT_CODE_RE.search(message or "")
    device_match = _DEVICE_RE.search(message or "")
    normalized = (message or "").strip()
    return {
        "user_message": normalized,
        "user_identity": user_identity,
        "equipment_hint": device_match.group(1) if device_match else None,
        "metric_hint": None,
        "fault_code_hint": fault_code_match.group(1).upper() if fault_code_match else None,
        "time_range_hint": "最近" if "最近" in normalized or "当前" in normalized else None,
        "analysis_goal": normalized or "故障诊断",
        "needs_sql": has_any(normalized, SQL_KEYWORDS),
        "needs_knowledge": bool(fault_code_match) or has_any(normalized, KNOWLEDGE_KEYWORDS),
        "needs_report": has_any(normalized, REPORT_KEYWORDS),
        "report_format": "markdown",
    }


def decide_capabilities(
    *,
    payload: dict[str, Any],
    request: DiagnosisRequest,
    message: str,
    report_from_previous_artifact: bool,
) -> SingleAgentDecision:
    normalized = (request.user_message or message or "").strip()
    payload_sql = payload.get("needs_sql")
    payload_knowledge = payload.get("needs_knowledge")

    needs_sql = bool(payload_sql) if isinstance(payload_sql, bool) else has_any(normalized, SQL_KEYWORDS)
    needs_knowledge = (
        bool(payload_knowledge)
        if isinstance(payload_knowledge, bool)
        else bool(request.fault_code_hint) or has_any(normalized, KNOWLEDGE_KEYWORDS)
    )
    needs_report = bool(request.needs_report) or has_any(normalized, REPORT_KEYWORDS)

    if report_from_previous_artifact:
        return SingleAgentDecision(
            needs_sql=False,
            needs_knowledge=False,
            needs_report=True,
            report_from_previous_artifact=True,
            reason="识别到基于当前线程已有结果生成报告的请求",
        )

    reason_parts = [
        "需要 SQL" if needs_sql else "跳过 SQL",
        "需要知识库" if needs_knowledge else "跳过知识库",
        "需要报告" if needs_report else "跳过报告",
    ]
    return SingleAgentDecision(
        needs_sql=needs_sql,
        needs_knowledge=needs_knowledge,
        needs_report=needs_report,
        report_from_previous_artifact=False,
        reason="；".join(reason_parts),
    )
