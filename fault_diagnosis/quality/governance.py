"""Unified governance snapshot helpers for grounded diagnosis outputs."""

from __future__ import annotations

import re
from typing import Any


_ROUTE_LABELS = {
    "fault_diagnosis": "故障诊断",
    "status_inspection": "状态巡检",
    "manual_qa": "手册问答",
    "report_generation": "报告生成",
}

_GATE_STATUS_LABELS = {
    "pass": "已支撑",
    "review_required": "待复核",
    "blocked": "待确认",
}

_RISK_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
}

_FAULT_CODE_RE = re.compile(r"\b[A-Z]\d{4,}\b")
_DEVICE_CODE_RE = re.compile(r"\b[A-Z]{2,}(?:-\d+)+\b")
_ASCII_WORD_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b")
_CJK_PHRASE_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")

_GENERIC_CJK_STOPWORDS = {
    "当前",
    "建议",
    "可以",
    "需要",
    "可能",
    "存在",
    "相关",
    "结果",
    "说明",
    "处理",
    "分析",
    "诊断",
    "系统",
    "工作流",
    "数据",
    "知识",
    "报告",
    "正式报告",
    "初步报告",
}

_ASCII_STOPWORDS = {
    "select",
    "from",
    "where",
    "limit",
    "group",
    "order",
    "workflow",
    "report",
    "sql",
}

_DOMAIN_KEYWORDS = [
    "主轴过载",
    "主轴负载",
    "主轴电流",
    "主轴温度",
    "电机温度",
    "负载持续上升",
    "电流持续上升",
    "温度持续上升",
    "振动持续增大",
    "切削负载",
    "刀具磨损",
    "轴承阻力",
    "机械卡滞",
    "加工参数",
    "进给过高",
    "切深过大",
    "故障码",
    "报警记录",
    "实时数据",
    "现场数据",
    "振动",
    "温度",
    "电流",
    "负载",
    "主轴",
    "报警",
    "润滑不足",
    "冷却异常",
    "装夹异常",
    "程序段异常",
    "参数设置过高",
    "工艺条件变化",
    "切削参数异常",
    "刀具崩刃",
    "过载阈值",
    "电流异常",
    "温升",
]


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_route_result(route_result: Any) -> dict[str, Any]:
    if hasattr(route_result, "model_dump"):
        payload = route_result.model_dump()
    elif isinstance(route_result, dict):
        payload = dict(route_result)
    else:
        payload = {}

    workflow_type = str(payload.get("workflow_type") or "")
    return {
        "workflow_type": workflow_type,
        "workflow_label": _ROUTE_LABELS.get(workflow_type, workflow_type or "未识别"),
        "confidence": str(payload.get("confidence") or "low"),
        "reason": _compact_text(payload.get("reason")),
        "needs_sql": bool(payload.get("needs_sql")),
        "needs_knowledge": bool(payload.get("needs_knowledge")),
        "needs_report": bool(payload.get("needs_report")),
    }


def _extract_keywords(*parts: Any) -> list[str]:
    text = " ".join(_compact_text(part) for part in parts if _compact_text(part))
    if not text:
        return []

    keywords: list[str] = []
    seen: set[str] = set()

    def _push(token: str) -> None:
        normalized = _compact_text(token)
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        keywords.append(normalized)

    for keyword in _DOMAIN_KEYWORDS:
        if keyword in text:
            _push(keyword)

    for pattern in (_FAULT_CODE_RE, _DEVICE_CODE_RE):
        for matched in pattern.findall(text):
            _push(matched)

    for matched in _ASCII_WORD_RE.findall(text):
        lowered = matched.lower()
        if lowered in _ASCII_STOPWORDS:
            continue
        _push(matched)

    for matched in _CJK_PHRASE_RE.findall(text):
        if matched in _GENERIC_CJK_STOPWORDS:
            continue
        _push(matched)

    return keywords[:12]


def _score_keyword_overlap(
    finding_keywords: list[str],
    evidence_keywords: list[str],
    evidence_type: str,
) -> tuple[list[str], int]:
    if not finding_keywords or not evidence_keywords:
        return [], 0

    evidence_lookup = {item.lower(): item for item in evidence_keywords}
    matched = [token for token in finding_keywords if token.lower() in evidence_lookup]
    score = int(round((len(matched) / max(1, len(finding_keywords))) * 100))
    if matched and evidence_type == "sql":
        score = min(100, score + 10)
    return matched[:6], score


def _build_findings_summary(findings: list[dict[str, Any]] | None) -> dict[str, Any]:
    finding_items = [item for item in (findings or []) if isinstance(item, dict)]
    total = len(finding_items)
    supported = 0
    pending = 0

    normalized_findings: list[dict[str, Any]] = []
    for item in finding_items:
        metadata = item.get("metadata") or {}
        grounding_status = str(metadata.get("grounding_status") or "grounded")
        state = "已支撑" if grounding_status == "grounded" else "待确认"
        if grounding_status == "grounded":
            supported += 1
        else:
            pending += 1
        normalized_findings.append(
            {
                "finding_id": item.get("finding_id"),
                "text": _compact_text(item.get("text")),
                "status": grounding_status,
                "status_label": state,
                "confidence": str(item.get("confidence") or "unknown"),
            }
        )

    if total == 0:
        overall = "pending"
        overall_label = "待确认"
    elif pending == 0:
        overall = "supported"
        overall_label = "已支撑"
    elif supported == 0:
        overall = "pending"
        overall_label = "待确认"
    else:
        overall = "partial"
        overall_label = "部分已支撑"

    return {
        "total": total,
        "supported": supported,
        "pending": pending,
        "status": overall,
        "status_label": overall_label,
        "items": normalized_findings,
    }


def _build_report_gate(evidence_quality: dict[str, Any] | None) -> dict[str, Any]:
    summary = dict(evidence_quality or {})
    gate = str(summary.get("gate") or "review_required")
    preliminary_allowed = gate in {"pass", "review_required"}
    formal_allowed = gate == "pass"

    if formal_allowed:
        code = "formal_allowed"
        label = "可出正式报告"
    elif preliminary_allowed:
        code = "preliminary_allowed"
        label = "可出初步报告"
    else:
        code = "report_blocked"
        label = "暂不建议出报告"

    return {
        "code": code,
        "label": label,
        "preliminary_report": "allowed" if preliminary_allowed else "blocked",
        "preliminary_report_label": "可出初步报告" if preliminary_allowed else "暂不建议出报告",
        "formal_report": "allowed" if formal_allowed else "blocked",
        "formal_report_label": "可出正式报告" if formal_allowed else "不可出正式报告",
        "gate": gate,
        "gate_label": _GATE_STATUS_LABELS.get(gate, gate),
        "release_ready": bool(summary.get("release_ready")),
        "risk_level": str(summary.get("risk_level") or "medium"),
        "risk_level_label": _RISK_LABELS.get(str(summary.get("risk_level") or "medium"), "中"),
        "review_reasons": list(summary.get("review_reasons") or []),
        "recommended_action": _compact_text(summary.get("recommended_action")),
    }


def _build_work_order_gate(action_guard: dict[str, Any] | None) -> dict[str, Any]:
    guard = dict(action_guard or {})
    publication_status = str(guard.get("publication_status") or "draft")
    allowed = publication_status == "published"
    return {
        "status": "allowed" if allowed else "blocked",
        "status_label": "可直接下发工单" if allowed else "暂不下发工单",
        "publication_status": publication_status,
        "publication_status_label": "正式输出" if allowed else "保留为审核记录",
        "final_filename": guard.get("final_filename"),
        "target_filename": guard.get("target_filename"),
    }


def build_governance_snapshot(
    *,
    route_result: Any = None,
    evidence_quality: dict[str, Any] | None = None,
    findings: list[dict[str, Any]] | None = None,
    action_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route = _normalize_route_result(route_result)
    findings_summary = _build_findings_summary(findings)
    report_gate = _build_report_gate(evidence_quality)
    work_order_gate = _build_work_order_gate(action_guard)
    evidence_quality = dict(evidence_quality or {})

    return {
        "route_result": route,
        "evidence_quality": {
            **evidence_quality,
            "gate_label": _GATE_STATUS_LABELS.get(str(evidence_quality.get("gate") or "review_required"), "待复核"),
            "risk_level_label": _RISK_LABELS.get(str(evidence_quality.get("risk_level") or "medium"), "中"),
        },
        "findings": findings_summary,
        "report_gate": report_gate,
        "work_order_gate": work_order_gate,
        "summary_text": "；".join(
            [
                part
                for part in (
                    findings_summary.get("status_label"),
                    report_gate.get("preliminary_report_label"),
                    report_gate.get("formal_report_label"),
                )
                if part
            ]
        ),
    }


def build_workflow_governance_snapshot(
    *,
    route_result: Any = None,
    finding_text: str = "",
    confidence: str = "medium",
    has_sql: bool = False,
    has_knowledge: bool = False,
    knowledge_required: bool = False,
    extra_review_reasons: list[str] | None = None,
) -> dict[str, Any]:
    review_reasons = list(extra_review_reasons or [])
    gate = "pass"
    risk_level = "low"

    if not has_sql:
        gate = "blocked"
        risk_level = "high"
        review_reasons.append("当前工作流未拿到结构化数据证据。")
    elif knowledge_required and not has_knowledge:
        gate = "review_required"
        risk_level = "medium"
        review_reasons.append("当前工作流缺少知识库补充，建议复核后再正式输出。")
    elif str(confidence or "medium").lower() != "high":
        gate = "review_required"
        risk_level = "medium"
        review_reasons.append("当前结论置信度还不够高，建议先保守输出。")

    evidence_quality = {
        "gate": gate,
        "risk_level": risk_level,
        "release_ready": gate == "pass",
        "review_reasons": review_reasons,
        "recommended_action": (
            "可以继续正式输出。"
            if gate == "pass"
            else "建议保留为初步结论，继续补数据或补知识依据。"
        ),
        "total_findings": 1 if _compact_text(finding_text) else 0,
        "linked_findings": 1 if has_sql and _compact_text(finding_text) else 0,
        "unsupported_findings": 0 if has_sql and _compact_text(finding_text) else (1 if _compact_text(finding_text) else 0),
        "low_confidence_findings": 1 if str(confidence or "").lower() == "low" and _compact_text(finding_text) else 0,
        "medium_confidence_findings": 1 if str(confidence or "").lower() == "medium" and _compact_text(finding_text) else 0,
        "high_confidence_findings": 1 if str(confidence or "").lower() == "high" and _compact_text(finding_text) else 0,
        "total_evidences": int(bool(has_sql)) + int(bool(has_knowledge)),
        "coverage_ratio": 1.0 if has_sql and _compact_text(finding_text) else 0.0,
        "coverage_summary": {
            "grade": "A" if gate == "pass" else "B" if gate == "review_required" else "D",
            "score": 95 if gate == "pass" else 78 if gate == "review_required" else 32,
            "metrics": [
                {"label": "SQL coverage", "value": "Yes" if has_sql else "No"},
                {"label": "RAG coverage", "value": "Yes" if has_knowledge else "No"},
                {"label": "Finding binding", "value": "1/1" if has_sql and _compact_text(finding_text) else "0/1"},
            ],
        },
    }
    findings = []
    if _compact_text(finding_text):
        findings.append(
            {
                "finding_id": "workflow-finding-1",
                "text": _compact_text(finding_text),
                "confidence": confidence,
                "metadata": {
                    "grounding_status": "grounded" if has_sql else "pending",
                },
            }
        )

    return build_governance_snapshot(
        route_result=route_result,
        evidence_quality=evidence_quality,
        findings=findings,
    )


def build_workflow_evidence_bundle(
    *,
    route_result: Any = None,
    finding_text: str = "",
    confidence: str = "medium",
    has_sql: bool = False,
    sql_title: str = "",
    sql_summary: str = "",
    sql_query: str = "",
    has_knowledge: bool = False,
    knowledge_title: str = "",
    knowledge_summary: str = "",
    knowledge_query: str = "",
    knowledge_required: bool = False,
    extra_review_reasons: list[str] | None = None,
) -> dict[str, Any]:
    evidence_records_snapshot: list[dict[str, Any]] = []

    if _compact_text(sql_summary):
        evidence_records_snapshot.append(
            {
                "evidence_id": "workflow-sql-1",
                "type": "sql",
                "title": _compact_text(sql_title) or "Workflow SQL 结果",
                "summary": _compact_text(sql_summary),
                "metadata": {"query": _compact_text(sql_query)},
            }
        )
    if _compact_text(knowledge_summary):
        evidence_records_snapshot.append(
            {
                "evidence_id": "workflow-rag-1",
                "type": "rag",
                "title": _compact_text(knowledge_title) or "Workflow 知识检索结果",
                "summary": _compact_text(knowledge_summary),
                "metadata": {"query": _compact_text(knowledge_query)},
            }
        )

    finding_keywords = _extract_keywords(finding_text)
    matched_evidence_ids: list[str] = []
    matched_keywords_acc: list[str] = []
    best_match_score = 0

    for record in evidence_records_snapshot:
        metadata = record.get("metadata") or {}
        evidence_keywords = _extract_keywords(
            record.get("title"),
            record.get("summary"),
            metadata.get("query"),
        )
        matched_keywords, score = _score_keyword_overlap(
            finding_keywords,
            evidence_keywords,
            str(record.get("type") or ""),
        )
        if score <= 0 and record.get("type") == "sql" and has_sql and _compact_text(finding_text):
            matched_keywords = finding_keywords[:3]
            score = 35
        if score > 0:
            matched_evidence_ids.append(record["evidence_id"])
            best_match_score = max(best_match_score, score)
            for token in matched_keywords:
                if token not in matched_keywords_acc:
                    matched_keywords_acc.append(token)

    effective_has_sql = any(
        item.get("type") == "sql" and item.get("evidence_id") in matched_evidence_ids
        for item in evidence_records_snapshot
    )
    effective_has_knowledge = any(
        item.get("type") == "rag" and item.get("evidence_id") in matched_evidence_ids
        for item in evidence_records_snapshot
    )

    governance = build_workflow_governance_snapshot(
        route_result=route_result,
        finding_text=finding_text,
        confidence=confidence,
        has_sql=effective_has_sql,
        has_knowledge=effective_has_knowledge or (has_knowledge and not knowledge_required),
        knowledge_required=knowledge_required,
        extra_review_reasons=extra_review_reasons,
    )
    report_gate_summary = dict(governance.get("evidence_quality") or {})
    strong_binding = bool(matched_evidence_ids) and best_match_score >= 45
    if _compact_text(finding_text):
        report_gate_summary["linked_findings"] = 1 if strong_binding else 0
        report_gate_summary["unsupported_findings"] = 0 if strong_binding else 1
        report_gate_summary["coverage_ratio"] = 1.0 if strong_binding else 0.0
        coverage_summary = dict(report_gate_summary.get("coverage_summary") or {})
        coverage_summary["metrics"] = [
            {"label": "SQL coverage", "value": "Yes" if effective_has_sql else "No"},
            {"label": "RAG coverage", "value": "Yes" if effective_has_knowledge else "No"},
            {"label": "Finding binding", "value": "1/1" if strong_binding else "0/1"},
        ]
        report_gate_summary["coverage_summary"] = coverage_summary

        if not matched_evidence_ids:
            report_gate_summary["gate"] = "blocked"
            report_gate_summary["risk_level"] = "high"
            report_gate_summary["release_ready"] = False
            report_gate_summary["review_reasons"] = list(report_gate_summary.get("review_reasons") or []) + [
                "当前 finding 还没有匹配到可用证据。"
            ]
            report_gate_summary["recommended_action"] = "建议继续补 SQL 或知识证据，再决定是否输出报告。"
        elif best_match_score < 45:
            report_gate_summary["gate"] = "blocked"
            report_gate_summary["risk_level"] = "high"
            report_gate_summary["release_ready"] = False
            report_gate_summary["review_reasons"] = list(report_gate_summary.get("review_reasons") or []) + [
                f"当前 finding 与证据的匹配分数偏低（score={best_match_score}），还不足以支撑稳定结论。"
            ]
            report_gate_summary["recommended_action"] = "建议继续补结构化数据或补更贴近的知识证据。"
        elif best_match_score < 70:
            report_gate_summary["gate"] = "review_required"
            report_gate_summary["risk_level"] = "medium"
            report_gate_summary["release_ready"] = False
            report_gate_summary["review_reasons"] = list(report_gate_summary.get("review_reasons") or []) + [
                f"当前 finding 与证据已有关联，但匹配分数仍一般（score={best_match_score}），建议先保守输出。"
            ]
            report_gate_summary["recommended_action"] = "可以先出初步报告，但不建议直接下正式结论。"
        else:
            existing_reasons = list(report_gate_summary.get("review_reasons") or [])
            report_gate_summary["review_reasons"] = [
                reason
                for reason in existing_reasons
                if "匹配分数" not in str(reason)
            ]

    findings_snapshot: list[dict[str, Any]] = []
    finding_links_snapshot: list[dict[str, Any]] = []

    if _compact_text(finding_text):
        findings_snapshot.append(
            {
                "finding_id": "workflow-finding-1",
                "text": _compact_text(finding_text),
                "severity": "high" if report_gate_summary.get("risk_level") == "high" else "medium",
                "confidence": confidence,
                "metadata": {
                    "grounding_status": "grounded" if strong_binding else "pending",
                    "display_text": _compact_text(finding_text),
                    "evidence_bound": strong_binding,
                    "match_score": best_match_score,
                },
            }
        )
        finding_links_snapshot.append(
            {
                "finding_id": "workflow-finding-1",
                "evidence_ids": matched_evidence_ids,
                "matched_keywords": matched_keywords_acc[:6],
                "match_score": best_match_score,
            }
        )

    return {
        "governance": build_governance_snapshot(
            route_result=route_result,
            evidence_quality=report_gate_summary,
            findings=findings_snapshot,
        ),
        "report_gate_summary": report_gate_summary,
        "findings_snapshot": findings_snapshot,
        "finding_links_snapshot": finding_links_snapshot,
        "evidence_records_snapshot": evidence_records_snapshot,
    }
