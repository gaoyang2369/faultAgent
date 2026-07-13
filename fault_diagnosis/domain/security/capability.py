"""Role-level capability authorization before context clarification."""

from __future__ import annotations

from .contracts import AuthContext, AuthorizationDecision
from .permissions import (
    WORKFLOW_ACTION_REQUEST,
    WORKFLOW_FAULT_DIAGNOSIS,
    WORKFLOW_HEALTH_ASSESSMENT,
    WORKFLOW_KNOWLEDGE_QA,
    WORKFLOW_REPORT_GENERATION,
    WORKFLOW_ROOT_CAUSE_ANALYSIS,
    WORKFLOW_STATUS_QUERY,
)


_PERMISSION_BY_CAPABILITY = {
    "explain_fault_code": WORKFLOW_KNOWLEDGE_QA,
    "expand_previous_answer": WORKFLOW_KNOWLEDGE_QA,
    "show_manual_fields": WORKFLOW_KNOWLEDGE_QA,
    "check_runtime_status": WORKFLOW_STATUS_QUERY,
    "compare_runtime_status": WORKFLOW_STATUS_QUERY,
    "diagnose_fault": WORKFLOW_FAULT_DIAGNOSIS,
    "diagnose_from_runtime": WORKFLOW_FAULT_DIAGNOSIS,
    "resolution_recommendation": WORKFLOW_FAULT_DIAGNOSIS,
    "health_assessment": WORKFLOW_HEALTH_ASSESSMENT,
    "root_cause_analysis": WORKFLOW_ROOT_CAUSE_ANALYSIS,
    "generate_report": WORKFLOW_REPORT_GENERATION,
    "generate_report_from_previous": WORKFLOW_REPORT_GENERATION,
    "decide_workorder": WORKFLOW_ACTION_REQUEST,
    "create_workorder_draft": WORKFLOW_ACTION_REQUEST,
    "refresh_then_decide_workorder": WORKFLOW_ACTION_REQUEST,
}


def authorize_capability_preflight(auth: AuthContext, capability: str) -> AuthorizationDecision:
    permission = _PERMISSION_BY_CAPABILITY.get(capability)
    if not permission or auth.has_permission(permission):
        return AuthorizationDecision(allowed=True, mode="allow", reason="Capability preflight passed.")
    if auth.role == "guest" and capability in {"generate_report", "generate_report_from_previous"}:
        return AuthorizationDecision(
            allowed=True,
            mode="degrade",
            reason="当前身份无报告生成权限，允许降级为运行状态摘要。",
            denied_reason_code="report_permission_denied",
            user_message="当前身份不能生成正式报告，已降级为运行状态摘要。",
        )
    if capability in {"decide_workorder", "create_workorder_draft", "refresh_then_decide_workorder"}:
        code = "workorder_permission_denied"
        message = "当前身份无维修工单判断或草稿生成权限。"
    elif capability == "root_cause_analysis":
        code = "root_cause_permission_denied"
        message = "当前身份无根因分析权限。"
    elif capability == "health_assessment":
        code = "health_assessment_permission_denied"
        message = "当前身份无健康评估权限。"
    else:
        code = "diagnosis_permission_denied"
        message = "当前身份无法进行故障诊断。游客仅可查看授权设备运行状态或查询公开故障码。"
    return AuthorizationDecision(
        allowed=False,
        mode="deny",
        reason=message,
        denied_reason_code=code,
        user_message=message,
    )


__all__ = ["authorize_capability_preflight"]
