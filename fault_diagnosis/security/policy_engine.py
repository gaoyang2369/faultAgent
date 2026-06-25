"""RBAC + resource-scope decisions at the workflow boundary."""

from __future__ import annotations

from typing import Any

from .contracts import AuthContext, AuthorizationDecision
from .assets import asset_is_in_scope
from .permissions import effective_resource_scope

WORKFLOW_PERMISSION_BY_TASK = {
    "knowledge_qa": "workflow.knowledge_qa",
    "status_query": "workflow.status_query",
    "alarm_triage": "workflow.alarm_triage",
    "fault_diagnosis": "workflow.fault_diagnosis",
    "root_cause_analysis": "workflow.root_cause_analysis",
    "health_assessment": "workflow.health_assessment",
    "report_generation": "workflow.report_generation",
    "action_request": "workflow.action_request",
    "permission_scope_query": "workflow.permission_scope_query",
}

_GUEST_DEGRADABLE_TASKS = {
    "fault_diagnosis",
    "root_cause_analysis",
    "health_assessment",
}


def _requested_assets(decision: Any) -> list[str]:
    objects = getattr(decision, "objects", {}) or {}
    return [str(value).strip() for value in objects.get("device_ids", []) if str(value).strip()]


def authorize_workflow(auth: AuthContext, decision: Any) -> AuthorizationDecision:
    task_type = str(getattr(decision, "primary_task_type", "status_query") or "status_query")
    permission = WORKFLOW_PERMISSION_BY_TASK.get(task_type)
    resource_scope = effective_resource_scope(auth)
    data_scope = resource_scope.model_dump()
    data_scope["role"] = auth.role
    kb_scope = {"allowed_visibility": list(resource_scope.allowed_kb_visibility)}
    requested_assets = _requested_assets(decision)
    if auth.role in {"guest", "engineer"}:
        if not auth.asset_scope and not auth.system_scope:
            return AuthorizationDecision(
                allowed=False,
                mode="clarify",
                reason="当前账号未配置设备或系统范围。",
                denied_reason_code="missing_resource_scope",
                data_scope=data_scope,
                kb_scope=kb_scope,
                user_message="当前账号尚未配置可访问设备或系统范围，请联系管理员。",
            )
        denied_assets = [asset for asset in requested_assets if not asset_is_in_scope(asset, auth.asset_scope)]
        if denied_assets:
            return AuthorizationDecision(
                allowed=False,
                mode="deny",
                reason=f"请求设备不在授权范围：{', '.join(denied_assets)}",
                denied_reason_code="asset_out_of_scope",
                data_scope=data_scope,
                kb_scope=kb_scope,
                user_message="请求中的设备不在当前账号负责范围内。",
            )

    if permission and not auth.has_permission(permission):
        if auth.role == "guest" and task_type == "report_generation":
            return AuthorizationDecision(
                allowed=False,
                mode="deny",
                reason="当前身份无报告生成权限。",
                denied_reason_code="report_permission_denied",
                denied_nodes={"report": "missing_report_permission"},
                data_scope=data_scope,
                kb_scope=kb_scope,
                user_message=(
                    "当前身份无法生成 DCMA 运行报告。"
                    "游客仅可查看授权设备最近一小时运行状态，不能生成诊断报告、运行报告或根因结论。"
                ),
            )
        if auth.role == "guest" and task_type in _GUEST_DEGRADABLE_TASKS:
            denied_nodes = {
                "fault_diagnosis": "missing_workflow_permission",
                "root_cause_analysis": "missing_workflow_permission",
                "health_assessment": "missing_workflow_permission",
                "report": "missing_report_permission",
                "workorder_decision": "missing_workorder_permission",
            }
            return AuthorizationDecision(
                allowed=True,
                mode="degrade",
                reason="当前身份仅可查看 real_data_01 最近一小时状态和公开处理意见。",
                denied_reason_code="workflow_degraded_for_guest",
                allowed_nodes={"sql": True, "knowledge": True, "analysis": True},
                denied_nodes=denied_nodes,
                runtime_tools=["sql_db_query_checker", "sql_db_query", "query_knowledge_base"],
                data_scope=data_scope,
                kb_scope=kb_scope,
                user_message="当前身份可查看最近一小时数据和公开处理意见，但不能进行故障诊断或生成诊断报告。",
            )
        return AuthorizationDecision(
            allowed=False,
            mode="deny",
            reason=f"当前角色缺少权限：{permission or 'workflow.unknown'}",
            denied_reason_code="missing_workflow_permission",
            denied_nodes={task_type: "missing_workflow_permission"},
            data_scope=data_scope,
            kb_scope=kb_scope,
            user_message="当前身份无权执行该任务，请登录具备相应权限的账号。",
        )

    allowed_nodes = dict(getattr(decision, "enabled_nodes", {}) or {})
    runtime_tools = list(getattr(decision, "runtime_tools", []) or [])
    denied_nodes: dict[str, str] = {}
    if auth.role == "guest":
        for node in ("report", "workorder_decision"):
            if allowed_nodes.get(node):
                denied_nodes[node] = "missing_node_permission"
            allowed_nodes[node] = False
        runtime_tools = [tool for tool in runtime_tools if tool != "save_report"]
    return AuthorizationDecision(
        allowed=True,
        mode="allow",
        reason="身份与资源范围校验通过。",
        allowed_nodes={key: bool(value) for key, value in allowed_nodes.items()},
        denied_nodes=denied_nodes,
        runtime_tools=runtime_tools,
        data_scope=data_scope,
        kb_scope=kb_scope,
    )


def apply_authorization_to_decision(decision: Any, authorization: AuthorizationDecision) -> Any:
    """Apply an authorization result without coupling the policy engine to Agent models."""

    decision.authorization = authorization.model_dump()
    decision.access_scope = dict(authorization.data_scope)
    decision.denied_nodes = dict(authorization.denied_nodes)
    if authorization.mode == "degrade":
        decision.needs_sql = True
        decision.needs_knowledge = True
        decision.needs_report = False
        decision.report_from_previous_artifact = False
        decision.enabled_nodes = {
            **dict(getattr(decision, "enabled_nodes", {}) or {}),
            "sql": True,
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": False,
            "workorder_decision": False,
            "report": False,
        }
        decision.runtime_tools = list(authorization.runtime_tools)
        decision.guardrails = list(
            dict.fromkeys(
                [
                    *list(getattr(decision, "guardrails", []) or []),
                    "guest_uses_real_data_01_only",
                    "guest_uses_last_one_hour_only",
                    "guest_has_no_diagnosis_claims",
                    "permission_denial_disclosed",
                ]
            )
        )
    elif authorization.allowed:
        decision.enabled_nodes = dict(authorization.allowed_nodes)
        decision.runtime_tools = list(authorization.runtime_tools)
    else:
        decision.enabled_nodes = {}
        decision.runtime_tools = []
    return decision
