"""Workflow thin adapters over existing tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any


def _stringify_tool_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


async def invoke_tool(tool: Any, payload: Any) -> Any:
    """Invoke a LangChain tool through a unified async interface."""

    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(payload)
    if hasattr(tool, "invoke"):
        return await asyncio.to_thread(tool.invoke, payload)
    return await asyncio.to_thread(tool, payload)


def get_current_time_text() -> str:
    """Get the current time text using the existing utility tool."""

    from ..tools.utility_tools import get_time

    return get_time.invoke({}) if hasattr(get_time, "invoke") else get_time()


def query_knowledge_text(query: str) -> str:
    """Query the knowledge-base tool and return plain text."""

    from ..tools.kb_tools import query_knowledge_base

    result = query_knowledge_base.invoke({"query": query}) if hasattr(query_knowledge_base, "invoke") else query_knowledge_base(query)
    return _stringify_tool_output(result)


def build_sql_tools_map() -> dict[str, Any]:
    """Build a name -> SQL tool mapping."""

    from ..tools.sql_tools import get_sqltools

    tools_map: dict[str, Any] = {}
    for tool in get_sqltools():
        tool_name = getattr(tool, "name", "").strip()
        if tool_name:
            tools_map[tool_name] = tool
    return tools_map


def find_sql_tool(tools_map: dict[str, Any], tool_name: str, required: bool = True) -> Any:
    """Find a SQL tool by name."""

    tool = tools_map.get(tool_name)
    if tool is None and required:
        available = ", ".join(sorted(tools_map))
        raise RuntimeError(f"未找到 SQL 工具：{tool_name}，当前可用工具：{available}")
    return tool


def save_markdown_report_from_analysis(
    *,
    title: str,
    report_time: str,
    diagnosis_object: str,
    diagnosis_type: str,
    executive_summary: str,
    diagnosis_overview: str,
    diagnosis_details: str,
    fault_inference: str,
    repair_recommendations: str,
    preventive_maintenance: str,
    diagnosis_basis: str,
    report_filename: str,
) -> str:
    """Save a markdown report through the existing report tool."""

    from ..tools.report_tools import save_report

    result = save_report.invoke(
        {
            "title": title,
            "report_time": report_time,
            "diagnosis_object": diagnosis_object,
            "diagnosis_type": diagnosis_type,
            "executive_summary": executive_summary,
            "diagnosis_overview": diagnosis_overview,
            "diagnosis_details": diagnosis_details,
            "fault_inference": fault_inference,
            "repair_recommendations": repair_recommendations,
            "preventive_maintenance": preventive_maintenance,
            "diagnosis_basis": diagnosis_basis,
            "report_filename": report_filename,
        }
    )
    return _stringify_tool_output(result)
