"""Thin adapters over existing tools for the single-agent diagnosis path."""

from __future__ import annotations

import asyncio
from typing import Any


async def invoke_tool(tool: Any, payload: Any) -> Any:
    """Invoke a LangChain tool through a unified async interface."""

    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(payload)
    if hasattr(tool, "invoke"):
        return await asyncio.to_thread(tool.invoke, payload)
    return await asyncio.to_thread(tool, payload)


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
