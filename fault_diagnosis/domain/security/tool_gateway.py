"""Defense-in-depth authorization for every restricted tool invocation."""

from __future__ import annotations

from typing import Any

from .contracts import AuthContext, AuthorizationDecision

TOOL_PERMISSION_MAP = {
    "sql_db_query_checker": "tool.sql.read",
    "sql_db_query": "tool.sql.read",
    "query_knowledge_base": "tool.kb.search",
    "save_report": "tool.report.write_draft",
    "create_workorder": "tool.workorder.create",
    "dispatch_workorder": "tool.workorder.dispatch",
}


def authorize_tool_call(
    auth: AuthContext,
    tool_name: str,
    tool_input: Any = None,
    decision: Any = None,
) -> AuthorizationDecision:
    del tool_input
    permission = TOOL_PERMISSION_MAP.get(tool_name)
    if permission is None:
        return AuthorizationDecision(
            allowed=False,
            mode="deny",
            reason=f"工具未配置权限映射：{tool_name}",
            denied_reason_code="unknown_tool_permission",
            denied_nodes={tool_name: "unknown_tool_permission"},
        )
    if not auth.has_permission(permission):
        return AuthorizationDecision(
            allowed=False,
            mode="deny",
            reason=f"当前角色缺少工具权限：{permission}",
            denied_reason_code="missing_tool_permission",
            denied_nodes={tool_name: "missing_tool_permission"},
        )
    runtime_tools = set(getattr(decision, "runtime_tools", []) or []) if decision is not None else set()
    if runtime_tools and tool_name not in runtime_tools:
        return AuthorizationDecision(
            allowed=False,
            mode="deny",
            reason=f"工具不在当前 workflow 授权范围：{tool_name}",
            denied_reason_code="tool_not_enabled_for_workflow",
            denied_nodes={tool_name: "tool_not_enabled_for_workflow"},
        )
    return AuthorizationDecision(allowed=True, mode="allow", reason="工具权限校验通过。")
