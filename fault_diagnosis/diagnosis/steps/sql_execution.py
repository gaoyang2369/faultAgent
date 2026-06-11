"""公共 SQL 执行 step。"""

from __future__ import annotations

from typing import Awaitable, Callable



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
