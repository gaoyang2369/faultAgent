"""General utility tools used by the agent runtime."""

from __future__ import annotations

from datetime import datetime
import os

from langchain_core.tools import tool


def _tavily_is_configured() -> bool:
    raw = os.getenv("TAVILY_API_KEY", "").strip()
    if not raw:
        return False
    if raw.startswith("replace_with_"):
        return False
    return True


@tool("search_tool")
def search_tool(query: str) -> str:
    """Search the web when Tavily is configured; otherwise return a safe hint."""
    if not _tavily_is_configured():
        return (
            "search_tool 当前不可用：未配置有效的 TAVILY_API_KEY。"
            "请先补齐 Tavily 配置，或优先使用 query_knowledge_base 进行本地知识检索。"
            f" 当前查询：{query}"
        )

    try:
        from langchain_tavily import TavilySearch

        tool_impl = TavilySearch(max_results=5, topic="general")
        result = tool_impl.invoke(query)
        return str(result)
    except Exception as exc:
        return (
            "search_tool 当前调用失败，已自动降级。"
            f" 原因：{exc}. 建议优先使用 query_knowledge_base，"
            "或检查 TAVILY_API_KEY / 网络连通性。"
        )


def get_search_tool():
    """Return the stable search wrapper tool."""
    return search_tool


@tool
def get_time() -> str:
    """Get current local time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
