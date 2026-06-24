"""Rule-based request understanding fallback and capability decisions."""

from __future__ import annotations

import re
from typing import Any

from ..diagnosis.contracts import DiagnosisRequest
from .context import ConversationDiagnosisState
from .contracts import SingleAgentDecision
from .workflow import analyze_evidence_gap, build_workflow_plan, route_task

_FAULT_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]\d{3,5})(?![A-Z0-9])", re.IGNORECASE)
_DEVICE_RE = re.compile(
    r"([A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机|[A-Z]+\d+电机\d+)",
    re.IGNORECASE,
)
_GENERIC_DCMA_HINTS = {
    "dcma",
    "dcma系统",
    "dcma 系统",
    "系统",
    "全系统",
    "当前系统",
}

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
    "运行",
    "运行情况",
    "运行状态",
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
    "异常码",
    "报警码",
    "告警码",
)

_LIGHTWEIGHT_TEXT_RE = re.compile(r"[\s,，.。!！?？;；:：、~～…·'\"“”‘’()（）\[\]【】{}<>《》-]+")

_GREETING_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "哈喽",
    "嗨",
    "你好",
    "你好呀",
    "您好",
    "您好呀",
    "在吗",
    "在不在",
    "有人吗",
    "早上好",
    "上午好",
    "中午好",
    "下午好",
    "晚上好",
}

_CAPABILITY_MESSAGES = {
    "help",
    "帮助",
    "你是谁",
    "你能做什么",
    "你可以做什么",
    "能帮我什么",
    "可以帮我什么",
    "怎么用",
    "如何使用",
}

_THANKS_MESSAGES = {
    "thanks",
    "thankyou",
    "谢谢",
    "谢谢你",
    "感谢",
    "多谢",
    "辛苦了",
}

_GREETING_REPLY = "你好，我是故障诊断智能助手。有什么可以帮助你的吗？你也可以直接告诉我设备型号、故障码或异常现象。"
_CAPABILITY_REPLY = "我是故障诊断智能助手，可以帮你分析故障码、设备异常、历史告警和指标趋势；在你明确要求时，也可以生成诊断报告。"
_THANKS_REPLY = "不客气，我会继续协助你排查故障。"


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def normalize_equipment_hint(value: Any) -> str | None:
    """Normalize generic system names away from concrete device filters."""

    if value is None:
        return None
    hint = str(value).strip()
    if not hint:
        return None
    compact = hint.replace(" ", "").lower()
    if compact in {item.replace(" ", "").lower() for item in _GENERIC_DCMA_HINTS}:
        return None
    return hint


def should_use_rule_based_understanding(message: str) -> bool:
    """Fast path for routine DCMA status, alarm, fault-code and report requests."""

    normalized = (message or "").strip()
    if not normalized:
        return False
    return has_any(normalized, SQL_KEYWORDS + KNOWLEDGE_KEYWORDS + REPORT_KEYWORDS)


def normalize_lightweight_message(message: str) -> str:
    """Normalize short social messages for deterministic fast-path matching."""

    return _LIGHTWEIGHT_TEXT_RE.sub("", (message or "").strip()).lower()


def build_lightweight_conversation_reply(message: str) -> str | None:
    """Return a direct reply for pure greetings or simple capability questions."""

    normalized = normalize_lightweight_message(message)
    if not normalized:
        return None
    if normalized in _GREETING_MESSAGES:
        return _GREETING_REPLY
    if normalized in _CAPABILITY_MESSAGES:
        return _CAPABILITY_REPLY
    if normalized in _THANKS_MESSAGES:
        return _THANKS_REPLY
    return None


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
    equipment_hint = normalize_equipment_hint(device_match.group(1) if device_match else None)
    return {
        "user_message": normalized,
        "user_identity": user_identity,
        "equipment_hint": equipment_hint,
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
    conversation_state: ConversationDiagnosisState | None = None,
) -> SingleAgentDecision:
    normalized = (request.user_message or message or "").strip()
    payload_sql = payload.get("needs_sql")
    payload_knowledge = payload.get("needs_knowledge")

    needs_report = bool(request.needs_report) or has_any(normalized, REPORT_KEYWORDS)
    route = route_task(
        payload=payload,
        message=normalized,
        report_from_previous_artifact=report_from_previous_artifact,
    )
    gap_plan = analyze_evidence_gap(route, conversation_state)
    _apply_evidence_gap_to_route(route, gap_plan)
    if isinstance(payload_sql, bool) and payload_sql:
        route.flags["need_sql"] = True
    if isinstance(payload_knowledge, bool) and payload_knowledge:
        route.flags["need_knowledge"] = True
    if needs_report:
        route.flags["need_report"] = True
    _apply_plan_mode_flags(route)

    plan = build_workflow_plan(route, needs_report=needs_report)
    needs_sql = plan.resolved_nodes.get("sql", False)
    needs_knowledge = plan.resolved_nodes.get("knowledge", False)
    needs_report = plan.resolved_nodes.get("report", False)

    if report_from_previous_artifact:
        return SingleAgentDecision(
            needs_sql=needs_sql,
            needs_knowledge=needs_knowledge,
            needs_report=True,
            report_from_previous_artifact=True,
            primary_task_type=route.primary_task_type.value,
            candidate_task_types=[item.value for item in route.candidate_task_types],
            intent_stack=route.intent_stack,
            context_resolution=route.context_resolution,
            active_case_id=conversation_state.active_case_id if conversation_state else None,
            relation_to_previous=route.relation_to_previous,
            plan_mode=route.plan_mode,
            evidence_mode=route.evidence_mode,
            referenced_artifact_id=route.referenced_artifact_id,
            referenced_case_id=route.referenced_case_id,
            required_evidence=route.required_evidence,
            satisfied_evidence=route.satisfied_evidence,
            missing_or_stale_evidence=route.missing_or_stale_evidence,
            should_refresh_runtime_data=route.should_refresh_runtime_data,
            action_target=route.action_target,
            route_confidence=route.route_confidence,
            user_goal=route.user_goal,
            objects=route.objects.model_dump(exclude_none=True),
            time_window=route.time_window.model_dump(exclude_none=True),
            subgoals=[item.model_dump(exclude_none=True) for item in route.subgoals],
            missing_slots=route.missing_slots,
            risk_level=route.risk_level,
            requested_output=route.requested_output,
            action_type=route.action_type,
            flags=route.flags,
            workflow_policy=plan.policy.model_dump(exclude_none=True),
            enabled_nodes=plan.resolved_nodes,
            runtime_tools=plan.runtime_tools,
            output_schema=plan.policy.output_schema,
            guardrails=plan.policy.guardrails,
            reason="识别到基于当前线程已有结果生成报告的请求",
        )

    reason_parts = [
        f"任务类型 {route.primary_task_type.value}",
        "需要 SQL" if needs_sql else "跳过 SQL",
        "需要知识库" if needs_knowledge else "跳过知识库",
        "需要报告" if needs_report else "跳过报告",
    ]
    if plan.metadata.get("blocked_subgoals"):
        reason_parts.append("存在可继续但需披露的 blocked subgoal")
    if route.intent_stack:
        reason_parts.append(f"意图栈 {', '.join(route.intent_stack)}")
    return SingleAgentDecision(
        needs_sql=needs_sql,
        needs_knowledge=needs_knowledge,
        needs_report=needs_report,
        report_from_previous_artifact=False,
        primary_task_type=route.primary_task_type.value,
        candidate_task_types=[item.value for item in route.candidate_task_types],
        intent_stack=route.intent_stack,
        context_resolution=route.context_resolution,
        active_case_id=conversation_state.active_case_id if conversation_state else None,
        relation_to_previous=route.relation_to_previous,
        plan_mode=route.plan_mode,
        evidence_mode=route.evidence_mode,
        referenced_artifact_id=route.referenced_artifact_id,
        referenced_case_id=route.referenced_case_id,
        required_evidence=route.required_evidence,
        satisfied_evidence=route.satisfied_evidence,
        missing_or_stale_evidence=route.missing_or_stale_evidence,
        should_refresh_runtime_data=route.should_refresh_runtime_data,
        action_target=route.action_target,
        route_confidence=route.route_confidence,
        user_goal=route.user_goal,
        objects=route.objects.model_dump(exclude_none=True),
        time_window=route.time_window.model_dump(exclude_none=True),
        subgoals=[item.model_dump(exclude_none=True) for item in route.subgoals],
        missing_slots=route.missing_slots,
        risk_level=route.risk_level,
        requested_output=route.requested_output,
        action_type=route.action_type,
        flags=route.flags,
        workflow_policy=plan.policy.model_dump(exclude_none=True),
        enabled_nodes=plan.resolved_nodes,
        runtime_tools=plan.runtime_tools,
        output_schema=plan.policy.output_schema,
        guardrails=plan.policy.guardrails,
        reason="；".join(reason_parts),
    )


def _apply_evidence_gap_to_route(route: Any, gap_plan: Any) -> None:
    route.plan_mode = gap_plan.plan_mode
    route.evidence_mode = gap_plan.evidence_mode
    route.relation_to_previous = gap_plan.relation_to_previous
    route.referenced_case_id = gap_plan.referenced_case_id
    route.referenced_artifact_id = gap_plan.referenced_artifact_id
    route.required_evidence = list(gap_plan.required_evidence)
    route.satisfied_evidence = list(gap_plan.satisfied_evidence)
    route.missing_or_stale_evidence = list(gap_plan.missing_or_stale_evidence)
    route.should_refresh_runtime_data = bool(gap_plan.should_refresh_runtime_data)
    if gap_plan.reason:
        route.flags["evidence_gap_reason"] = True
    if gap_plan.plan_mode in {"workorder_decision_from_artifact", "status_refresh_then_workorder", "new_diagnosis_then_workorder"}:
        if "workorder_decision" not in route.intent_stack:
            route.intent_stack.append("workorder_decision")
        route.action_target = "workorder"


def _apply_plan_mode_flags(route: Any) -> None:
    if route.plan_mode == "workorder_decision_from_artifact":
        route.flags.update(
            need_sql=False,
            need_knowledge=False,
            need_analysis=False,
            need_resolution=False,
            need_report=False,
            need_workorder_decision=True,
            need_permission_check=True,
            need_risk_check=True,
        )
    elif route.plan_mode == "status_refresh_then_workorder":
        route.flags.update(
            need_sql=True,
            need_knowledge=False,
            need_analysis=False,
            need_resolution=False,
            need_report=False,
            need_workorder_decision=True,
            need_permission_check=True,
            need_risk_check=True,
            lightweight_analysis=True,
        )
    elif route.plan_mode == "new_diagnosis_then_workorder":
        route.flags["need_workorder_decision"] = True
