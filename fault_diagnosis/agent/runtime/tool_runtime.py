"""Synchronous adapters for real V2 runtime tool nodes."""

from __future__ import annotations

from typing import Any


class ToolRuntime:
    """Small sync facade over existing LangChain/tool functions."""

    def __init__(self, *, sql_tools: dict[str, Any] | None = None) -> None:
        self._sql_tools = sql_tools

    @property
    def sql_tools(self) -> dict[str, Any]:
        if self._sql_tools is None:
            from fault_diagnosis.domain.diagnosis.adapters import build_sql_tools_map
            from fault_diagnosis.platform.tools.sql_tools import get_sqltools

            self._sql_tools = build_sql_tools_map(get_sqltools())
        return self._sql_tools

    def invoke_sql_tool(self, tool_name: str, payload: Any) -> Any:
        tool = self.sql_tools.get(tool_name)
        if tool is None:
            available = ", ".join(sorted(self.sql_tools))
            raise RuntimeError(f"未找到 SQL 工具：{tool_name}，当前可用工具：{available}")
        if hasattr(tool, "invoke"):
            return tool.invoke(payload)
        return tool(payload)

    def query_knowledge_base(self, query: str) -> str:
        from fault_diagnosis.platform.tools.kb_tools import query_knowledge_base

        if hasattr(query_knowledge_base, "invoke"):
            return query_knowledge_base.invoke({"query": query})
        return query_knowledge_base(query)

    def save_report(
        self,
        *,
        report_filename: str,
        chart_payload: str | None = None,
        operation_report_payload: str = "",
    ) -> str:
        from fault_diagnosis.platform.tools.report_tools import save_report

        payload = {
            "report_filename": report_filename,
            "chart_payload": chart_payload,
            "operation_report_payload": operation_report_payload,
        }
        if hasattr(save_report, "invoke"):
            return save_report.invoke(payload)
        return save_report(**payload)
