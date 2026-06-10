"""历史会话与 Todo 查询 HTTP 路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..common.logger import get_logger
from ..services.history_service import (
    HistoryService,
    build_history_page_payload,
    empty_todos_payload,
    filter_todos_by_status,
    history_title,
    parse_history_cursor,
    parse_history_limit,
    summarize_session_id,
    summarize_thread_id,
    summarize_todos,
)
from ..auth.session_scope import resolve_request_scope
from ._shared import json_response_with_scope

router = APIRouter()
_log = get_logger("api.history")

_summarize_session_id = summarize_session_id
_summarize_thread_id = summarize_thread_id
_history_title = history_title
_parse_history_limit = parse_history_limit
_parse_history_cursor = parse_history_cursor
_build_history_page_payload = build_history_page_payload
_empty_todos_payload = empty_todos_payload
_summarize_todos = summarize_todos
_filter_todos_by_status = filter_todos_by_status


def _history_service(request: Request) -> HistoryService:
    session_manager, session_id, _, legacy_bindings = resolve_request_scope(request)
    return HistoryService(
        app=request.app,
        session_manager=session_manager,
        session_id=session_id,
        legacy_bindings=legacy_bindings,
        logger=_log,
    )


@router.get("/ai/history/{type}")
async def get_chat_history(request: Request, type: str):
    """返回当前会话可访问的历史 thread_id 列表。"""
    query_params = request.query_params
    paged_response = any(key in query_params for key in ("limit", "cursor", "q"))
    history_limit = parse_history_limit(query_params.get("limit"))
    history_cursor = parse_history_cursor(query_params.get("cursor"))
    history_keyword = (query_params.get("q") or "").strip()[:80]
    payload = await _history_service(request).list_history(
        history_type=type,
        paged_response=paged_response,
        limit=history_limit,
        cursor=history_cursor,
        keyword=history_keyword,
    )
    return json_response_with_scope(request, payload)


@router.get("/ai/history/{type}/{chat_id}")
async def get_chat_messages(request: Request, type: str, chat_id: str):
    """返回指定会话的消息历史。"""
    payload = await _history_service(request).get_messages(history_type=type, chat_id=chat_id)
    return json_response_with_scope(request, payload)


@router.delete("/ai/history/{type}/{chat_id}")
async def delete_chat_history(request: Request, type: str, chat_id: str):
    """删除当前会话拥有的指定对话历史。"""
    try:
        payload = await _history_service(request).delete_history(history_type=type, chat_id=chat_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="删除咨询记录失败") from exc
    if not payload:
        raise HTTPException(status_code=404, detail="未找到可删除的咨询记录")
    return json_response_with_scope(request, payload)


@router.get("/api/todos/{thread_id}")
async def get_thread_todos(request: Request, thread_id: str, status: str | None = None):
    """获取特定对话的任务清单详情。"""
    payload = await _history_service(request).get_todos(thread_id=thread_id, status=status)
    return json_response_with_scope(request, payload)
