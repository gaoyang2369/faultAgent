"""公共 SQL 执行 step。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..contracts import SqlStepArtifact


async def build_sql_plan(
    prompt: str,
    invoke_json_model: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    default_summary: str,
) -> tuple[str, str]:
    """根据 SQL prompt 生成查询语句与摘要。"""

    payload = await invoke_json_model(prompt)
    sql_query = str(payload.get("sql_query") or payload.get("query") or "").strip()
    summary = str(payload.get("summary") or default_summary).strip() or default_summary
    return sql_query, summary


async def execute_sql_plan(
    sql_query: str,
    summary: str,
    *,
    build_sql_tools_map: Callable[[], dict[str, Any]],
    find_sql_tool: Callable[[dict[str, Any], str, bool], Any],
    invoke_tool: Callable[[Any, Any], Awaitable[Any]],
    stringify: Callable[[Any], str],
    preview: Callable[[Any], str],
) -> SqlStepArtifact:
    """执行统一 SQL 计划并返回标准化 artifact。"""

    tools_map = build_sql_tools_map()
    query_tool = find_sql_tool(tools_map, "sql_db_query", True)
    checker_tool = find_sql_tool(tools_map, "sql_db_query_checker", False)

    if checker_tool is not None:
        checked_query = await invoke_tool(checker_tool, {"query": sql_query})
        checked_query_text = stringify(checked_query).strip()
        if checked_query_text:
            sql_query = checked_query_text

    raw_output = await invoke_tool(query_tool, {"query": sql_query})
    return SqlStepArtifact(
        success=True,
        summary=summary,
        sql_used=[sql_query],
        result_preview=preview(raw_output),
        raw_output=stringify(raw_output),
    )
