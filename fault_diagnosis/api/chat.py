"""聊天与 Agent 兼容入口 HTTP 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..common.logger import get_logger
from ..services.chat_service import (
    AgentChatPayload,
    ChatService,
    StopStreamPayload,
    build_visual_action_from_stream_event,
    history_message_content,
    history_message_role,
    parse_sse_payloads,
    summarize_session_id,
    summarize_thread_id,
    to_langchain_history_message,
    to_langchain_history_messages,
    truncate_history_before_user_turn,
)
from ..agent_runtime.streaming import token_stream_events
from ._shared import json_response_with_scope

router = APIRouter()
_log = get_logger("api.chat")

_summarize_session_id = summarize_session_id
_summarize_thread_id = summarize_thread_id
_parse_sse_payloads = parse_sse_payloads
_build_visual_action_from_stream_event = build_visual_action_from_stream_event
_history_message_role = history_message_role
_history_message_content = history_message_content
_truncate_history_before_user_turn = truncate_history_before_user_turn
_to_langchain_history_message = to_langchain_history_message
_to_langchain_history_messages = to_langchain_history_messages


def _chat_service() -> ChatService:
    return ChatService(stream_events=token_stream_events, logger=_log)


@router.get("/chat/stream")
async def stream_chat_log_get(
    request: Request,
    message: str,
    thread_id: str | None = None,
    user_identity: str = "游客",
    stream_id: str | None = None,
):
    """
    GET 版本的流式端点，便于前端使用原生 EventSource 进行 SSE 连接。
    参数:
        message: 用户消息内容
        thread_id: 对话线程 ID（仅当属于当前服务端会话时才会复用）
        user_identity: 仅用于提示词分流，不作为权限边界
    """
    return await _chat_service().stream_chat(
        request,
        message=message,
        thread_id=thread_id,
        user_identity=user_identity,
        stream_id=stream_id,
    )


@router.get("/chat/stream/edit")
async def stream_chat_edit_get(
    request: Request,
    message: str,
    thread_id: str,
    user_turn_index: int,
    user_identity: str = "游客",
    stream_id: str | None = None,
):
    """编辑指定用户轮次后重新生成回答。"""
    return await _chat_service().stream_edit(
        request,
        message=message,
        thread_id=thread_id,
        user_turn_index=user_turn_index,
        user_identity=user_identity,
        stream_id=stream_id,
    )


@router.post("/agent/chat")
async def agent_chat(request: Request, payload: AgentChatPayload):
    """语音后端使用的非流式 JSON 兼容接口。"""
    return await _chat_service().agent_chat(request, payload)


@router.post("/chat/stop")
async def stop_chat_stream(request: Request, payload: StopStreamPayload):
    """停止当前会话中的活跃流。"""
    response_payload = await _chat_service().stop_stream(request, payload)
    return json_response_with_scope(request, response_payload)
