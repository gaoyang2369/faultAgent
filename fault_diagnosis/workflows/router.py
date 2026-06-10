"""Workflow 场景路由。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .boundary_specs import get_workflow_boundary_spec
from .contracts import WorkflowRouteResult, WorkflowType

_FAULT_KEYWORDS = (
    "故障",
    "异常",
    "告警",
    "报警",
    "诊断",
    "分析",
    "原因",
    "处置建议",
    "维修",
    "排查",
)
_STATUS_KEYWORDS = (
    "运行状态",
    "状态巡检",
    "巡检",
    "状态",
    "当前",
    "概览",
    "总览",
    "概况",
    "健康",
    "趋势",
    "风险",
    "指标",
    "当前状态",
)
_MANUAL_KEYWORDS = (
    "手册",
    "说明书",
    "操作说明",
    "安全注意事项",
    "注意事项",
    "是什么意思",
    "含义",
    "怎么操作",
    "如何操作",
    "维修步骤",
    "步骤",
)
_REPORT_KEYWORDS = (
    "生成报告",
    "导出报告",
    "整理成报告",
    "形成报告",
    "输出报告",
    "报告",
)
_REPORT_CONTEXT_KEYWORDS = (
    "上一轮",
    "上一条",
    "刚才",
    "上一次",
    "刚刚",
    "刚做的",
    "上一轮结果",
    "巡检结果",
    "诊断结果",
)
_DIAGNOSIS_INTENT_KEYWORDS = (
    "为什么",
    "为何",
    "为啥",
    "原因",
    "根因",
    "报警",
    "告警",
    "过载",
    "停机",
    "异常",
    "能不能直接出报告",
    "能不能出报告",
    "能不能下结论",
    "能不能确认",
    "能否直接出报告",
    "是否能确认",
    "是否可以确认",
    "现在能不能直接出报告",
)
_SQL_FIRST_DOMAIN_KEYWORDS = (
    "设备",
    "产线",
    "故障码",
    "报警",
    "告警",
    "过载",
    "停机",
    "主轴",
    "当前",
    "最近",
    "数据",
)
_EVIDENCE_KEYWORDS = (
    "证据链",
    "证据",
    "依据",
    "复核",
    "复查",
    "支撑",
    "覆盖率",
    "门禁",
    "站得住脚",
    "充分吗",
    "可靠吗",
    "哪里还不够",
    "哪些地方还不够",
)
_EVIDENCE_CONTEXT_KEYWORDS = (
    "刚才",
    "刚刚",
    "上一轮",
    "上一条",
    "这个结论",
    "该结论",
    "这个报告",
    "诊断结果",
    "巡检结果",
    "分析结果",
    "结论",
    "报告",
)
_CLARIFICATION_HINT_KEYWORDS = (
    "帮我看看",
    "帮我看一下",
    "看看",
    "看一下",
    "分析一下",
    "帮我分析",
    "这个问题",
    "这个异常",
    "这台设备",
    "这个设备",
    "有问题吗",
)
_METRIC_KEYWORDS = (
    "温度",
    "振动",
    "电流",
    "电压",
    "压力",
    "转速",
    "扭矩",
    "功率",
    "风险",
    "状态",
)
_TIME_RANGE_KEYWORDS = (
    "最近",
    "今天",
    "昨日",
    "昨天",
    "本周",
    "近24小时",
    "近48小时",
    "近7天",
    "过去",
)


@dataclass(frozen=True)
class _RouteScore:
    workflow_type: WorkflowType
    score: int
    reason: str


def _normalize_message(message: str) -> str:
    return (message or "").strip().lower()


def _contains_any(message: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in message for keyword in keywords if keyword)


def _count_keywords(message: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword and keyword in message)


def _is_report_only_request(message: str) -> bool:
    if not _contains_any(message, _REPORT_KEYWORDS):
        return False
    return _contains_any(message, _REPORT_CONTEXT_KEYWORDS)


def _is_evidence_review_request(message: str) -> bool:
    if "证据链" in message or "复核" in message or "复查" in message:
        return True
    if not _contains_any(message, _EVIDENCE_KEYWORDS):
        return False
    return _contains_any(message, _EVIDENCE_CONTEXT_KEYWORDS)


def _has_fault_signal(message: str) -> bool:
    return (
        re.search(r"\b[A-Z]\d{4,}\b", message, re.IGNORECASE) is not None
        or re.search(r"\b[A-Z]{2,}(?:-\d{1,})+\b", message, re.IGNORECASE) is not None
    )


def _has_strong_diagnosis_intent(message: str) -> bool:
    return _contains_any(message, _DIAGNOSIS_INTENT_KEYWORDS)


def _is_sql_first_reasoning_request(message: str) -> bool:
    has_reasoning_intent = _has_strong_diagnosis_intent(message)
    has_domain_signal = _contains_any(message, _SQL_FIRST_DOMAIN_KEYWORDS)
    return has_reasoning_intent and has_domain_signal


def _has_fault_code_hint(message: str) -> bool:
    return re.search(r"\b[a-z]\d{4,}\b", message, re.IGNORECASE) is not None


def _has_equipment_hint(message: str) -> bool:
    equipment_patterns = (
        r"\bdcma\b",
        r"\bj\d+\b",
        r"\b[a-z]{2,}(?:-\d{1,})+\b",
        r"(设备|机台|产线)[-_]?\d+",
        r"\d+号机",
    )
    return any(re.search(pattern, message) for pattern in equipment_patterns) or _contains_any(
        message,
        ("机械臂", "机器人", "主轴"),
    )


def _has_time_range_hint(message: str) -> bool:
    if _contains_any(message, _TIME_RANGE_KEYWORDS):
        return True
    return re.search(r"\d+\s*(分钟|小时|天|周|月)", message) is not None


def _has_metric_hint(message: str) -> bool:
    return _contains_any(message, _METRIC_KEYWORDS)


def _looks_generic_request(message: str) -> bool:
    if len(message) <= 8:
        return True
    if _contains_any(message, _CLARIFICATION_HINT_KEYWORDS):
        return True
    return message.endswith("怎么办") and not _has_fault_code_hint(message)


def _select_business_routes(message: str) -> list[_RouteScore]:
    report_only = _is_report_only_request(message)
    manual_score = _count_keywords(message, _MANUAL_KEYWORDS)
    fault_score = _count_keywords(message, _FAULT_KEYWORDS)
    status_score = _count_keywords(message, _STATUS_KEYWORDS)
    report_score = _count_keywords(message, _REPORT_KEYWORDS)
    diagnosis_intent = _has_strong_diagnosis_intent(message)
    fault_signal = _has_fault_signal(message)
    manual_preferred = manual_score > 0 and not diagnosis_intent and manual_score >= max(fault_score, status_score)

    scores: list[_RouteScore] = []
    if report_only:
        scores.append(
            _RouteScore(
                workflow_type=WorkflowType.REPORT_GENERATION,
                score=3,
                reason="识别到基于上一轮结果单独生成报告的请求",
            )
        )
        return scores

    if _is_sql_first_reasoning_request(message):
        return [
            _RouteScore(
                workflow_type=WorkflowType.FAULT_DIAGNOSIS,
                score=3,
                reason="识别到原因判断或可出报告性判断，按 SQL 优先诊断链路处理",
            )
        ]

    if manual_preferred:
        scores.append(
            _RouteScore(
                workflow_type=WorkflowType.MANUAL_QA,
                score=manual_score,
                reason="识别到手册问答、说明或安全注意事项类请求",
            )
        )
    if not manual_preferred and ((fault_signal and diagnosis_intent) or (fault_score > 0 and fault_score >= status_score)):
        scores.append(
            _RouteScore(
                workflow_type=WorkflowType.FAULT_DIAGNOSIS,
                score=max(fault_score, 2 if fault_signal else 0),
                reason="识别到故障码/设备信号与诊断意图，优先进入故障诊断流",
            )
        )
    if status_score > 0:
        scores.append(
            _RouteScore(
                workflow_type=WorkflowType.STATUS_INSPECTION,
                score=status_score,
                reason="识别到状态巡检、运行概览或风险摘要类请求",
            )
        )
    if report_score > 0:
        scores.append(
            _RouteScore(
                workflow_type=WorkflowType.REPORT_GENERATION,
                score=report_score,
                reason="识别到报告生成类请求",
            )
        )

    if not scores:
        return [
            _RouteScore(
                workflow_type=WorkflowType.MANUAL_QA,
                score=0,
                reason="未命中明确场景，先进入澄清流补齐关键信息",
            )
        ]

    return sorted(scores, key=lambda item: item.score, reverse=True)


def _build_candidate_workflows(scores: list[_RouteScore]) -> list[str]:
    top_score = scores[0].score
    candidates: list[str] = []
    for score in scores:
        if score.score < max(top_score - 1, 0):
            continue
        value = score.workflow_type.value
        if value not in candidates:
            candidates.append(value)
    return candidates or [WorkflowType.MANUAL_QA.value]


def _infer_missing_slots(message: str, candidate_workflows: list[str]) -> list[str]:
    missing_slots: list[str] = []
    has_equipment_hint = _has_equipment_hint(message)
    has_fault_code_hint = _has_fault_code_hint(message)
    has_time_range_hint = _has_time_range_hint(message)
    has_metric_hint = _has_metric_hint(message)

    if WorkflowType.FAULT_DIAGNOSIS.value in candidate_workflows:
        if not has_equipment_hint:
            missing_slots.append("equipment_hint")
        if not has_fault_code_hint and not has_metric_hint:
            missing_slots.append("fault_code_hint")

    if WorkflowType.STATUS_INSPECTION.value in candidate_workflows:
        if not has_equipment_hint and "equipment_hint" not in missing_slots:
            missing_slots.append("equipment_hint")
        if not has_time_range_hint:
            missing_slots.append("time_range_hint")

    return missing_slots


def _should_route_to_clarification(
    message: str,
    scores: list[_RouteScore],
    candidate_workflows: list[str],
    missing_slots: list[str],
) -> bool:
    primary_route = scores[0]
    second_score = scores[1].score if len(scores) > 1 else -1
    ambiguous = len(candidate_workflows) > 1 and primary_route.score - second_score <= 1
    primary_is_business_flow = primary_route.workflow_type in {
        WorkflowType.FAULT_DIAGNOSIS,
        WorkflowType.STATUS_INSPECTION,
    }

    if _looks_generic_request(message):
        return True
    if ambiguous:
        return True
    if primary_is_business_flow and missing_slots and not (_has_fault_code_hint(message) or _has_equipment_hint(message)):
        return True
    return False


def _build_route_result(
    workflow_type: WorkflowType,
    *,
    confidence: str,
    reason: str,
    candidate_workflows: list[str],
    missing_slots: list[str],
    disambiguation_needed: bool,
    review_needed: bool,
    needs_report: bool,
) -> WorkflowRouteResult:
    spec = get_workflow_boundary_spec(workflow_type)
    return WorkflowRouteResult(
        workflow_type=workflow_type,
        confidence=confidence,
        reason=reason,
        needs_sql="sql" in spec.required_capabilities,
        needs_knowledge="knowledge_base" in spec.required_capabilities,
        needs_report=needs_report,
        candidate_workflows=candidate_workflows,
        missing_slots=missing_slots,
        disambiguation_needed=disambiguation_needed,
        review_needed=review_needed,
        upstream_artifact_required=spec.supports_artifact_resume,
    )


def route_workflow_request(message: str, user_identity: str = "游客") -> WorkflowRouteResult:
    """根据用户消息路由到对应 Workflow 场景。"""

    normalized_message = _normalize_message(message)

    if _is_evidence_review_request(normalized_message):
        return _build_route_result(
            WorkflowType.EVIDENCE_REVIEW,
            confidence="high",
            reason="识别到显式证据质疑或复核请求，优先进入证据复核流",
            candidate_workflows=[WorkflowType.EVIDENCE_REVIEW.value],
            missing_slots=[],
            disambiguation_needed=False,
            review_needed=True,
            needs_report=False,
        )

    scores = _select_business_routes(normalized_message)
    candidate_workflows = _build_candidate_workflows(scores)
    missing_slots = _infer_missing_slots(normalized_message, candidate_workflows)
    primary_route = scores[0]

    if _should_route_to_clarification(normalized_message, scores, candidate_workflows, missing_slots):
        return _build_route_result(
            WorkflowType.CLARIFICATION,
            confidence="low",
            reason="当前请求存在歧义、缺少关键槽位或整体置信度不足，需先进入澄清流",
            candidate_workflows=candidate_workflows,
            missing_slots=missing_slots or ["equipment_hint"],
            disambiguation_needed=True,
            review_needed=False,
            needs_report=False,
        )

    confidence = "high" if primary_route.score >= 2 else "medium" if primary_route.score == 1 else "low"
    if user_identity.strip() == "管理员" and primary_route.workflow_type == WorkflowType.FAULT_DIAGNOSIS:
        confidence = "high"

    needs_report = primary_route.workflow_type == WorkflowType.REPORT_GENERATION or (
        get_workflow_boundary_spec(primary_route.workflow_type).default_needs_report
        and _contains_any(normalized_message, _REPORT_KEYWORDS)
    )

    return _build_route_result(
        primary_route.workflow_type,
        confidence=confidence,
        reason=primary_route.reason,
        candidate_workflows=[primary_route.workflow_type.value],
        missing_slots=[],
        disambiguation_needed=False,
        review_needed=False,
        needs_report=needs_report,
    )
