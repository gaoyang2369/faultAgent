"""tools/ package。"""

from .kb_tools import query_knowledge_base
from .report_tools import save_report, save_html_report
from .sql_tools import get_sqltools
from .utility_tools import get_time, get_search_tool


tools = [
    get_search_tool(),
    query_knowledge_base,
    save_report,
    save_html_report,
    get_time,
]


def get_runtime_tools():
    """返回当前部署应接入主 Agent 的工具列表。"""
    runtime_tools = list(tools)
    from .work_order_tools import create_work_order

    runtime_tools.append(create_work_order)
    return runtime_tools


__all__ = ["get_runtime_tools", "get_sqltools", "tools"]
