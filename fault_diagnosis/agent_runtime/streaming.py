"""聊天 SSE 兼容入口。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from fastapi import FastAPI

from .sse_adapter import adapt_sse_chunk, build_server_error_payload, build_trace_id, encode_sse_event
from ..runtime.dev_mode import stream_dev_chat_events
from .error_classification import classify_model_gateway_error
from .error_classification import model_error_code
from ..common.logger import bind_request_id, get_logger, new_request_id
from ..runtime.session_store import clear_namespace, set_namespace
from .stream_control import StreamCancellationHandle, clear_stream_handle
from ..common.utils import summarize_identifier_for_log
from ..single_agent import RestrictedSingleAgentRunner
from ..security.contracts import AuthContext

_log = get_logger("streaming")


def _build_trace_id(request_id: str) -> str:
    """为 SSE 会话构造稳定 trace 标识。"""

    return build_trace_id(request_id)


def _build_server_error_payload(
    *,
    message: str,
    error_id: str,
    trace_id: str,
    code: str = "INTERNAL_ERROR",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """构造兼容旧前端的结构化错误事件。"""

    return build_server_error_payload(
        message=message,
        error_id=error_id,
        trace_id=trace_id,
        code=code,
        retryable=retryable,
        details=details,
        run_id=run_id,
    )


def classify_stream_error(error: Exception) -> tuple[str, str]:
    """Classify scheduler-level errors without importing removed runtime paths."""

    error_text = str(error)
    lowered = error_text.lower()
    model_gateway_error = classify_model_gateway_error(error)
    if model_gateway_error:
        return model_gateway_error
    if "知识库" in error_text or "faiss" in lowered or "ollama" in lowered or "embedding" in lowered:
        return "knowledge_base", "知识库当前不可用，请先确认已完成预构建或稍后重试"
    if "tool" in lowered or "工具" in error_text:
        return "tool_execution", "工具执行失败，请稍后重试"
    return "internal", "请求处理失败，请稍后重试"


async def token_stream_events(
    app: FastAPI,
    message: str,
    thread_id: str,
    user_identity: str = "游客",
    request_id: str | None = None,
    stream_id: str | None = None,
    cancel_handle: StreamCancellationHandle | None = None,
    history_messages: list[Any] | None = None,
    replace_history: bool = False,
    auth_context: AuthContext | None = None,
    conversation_context: dict[str, Any] | None = None,
    complete_payload_enricher=None,
) -> AsyncGenerator[str, None]:
    """聊天 SSE 兼容入口：dev mock 或限制型单 Agent。"""

    request_id = bind_request_id(request_id or new_request_id())
    trace_id = _build_trace_id(request_id)
    stream_id = (stream_id or "").strip()
    set_namespace({"__builtins__": __builtins__})

    try:
        if getattr(app.state, "dev_mode", False):
            cancel_event = cancel_handle.cancel_event if cancel_handle else None
            async for chunk in stream_dev_chat_events(
                app,
                message,
                thread_id,
                user_identity,
                cancel_event=cancel_event,
                auth_context=auth_context,
            ):
                yield adapt_sse_chunk(
                    chunk,
                    trace_id,
                    thread_id=thread_id,
                    complete_payload_enricher=complete_payload_enricher,
                )
            return

        single_agent = RestrictedSingleAgentRunner(
            message=message,
            thread_id=thread_id,
            user_identity=user_identity,
            request_id=request_id,
            stream_id=stream_id,
            trace_id=trace_id,
            auth_context=auth_context,
            conversation_context=conversation_context,
        )
        async for chunk in single_agent.stream_events(
            app,
            cancel_handle=cancel_handle,
        ):
            yield adapt_sse_chunk(
                chunk,
                trace_id,
                thread_id=thread_id,
                complete_payload_enricher=complete_payload_enricher,
            )
    except asyncio.CancelledError:
        _log.warning(
            "流式请求被取消",
            thread_id=summarize_identifier_for_log(thread_id, keep=10),
            stream_id=summarize_identifier_for_log(stream_id, keep=8),
        )
        return
    except Exception as exc:
        error_id = request_id or new_request_id()
        error_category, error_message = classify_stream_error(exc)
        _log.exception(
            "聊天流调度失败",
            thread_id=summarize_identifier_for_log(thread_id, keep=10),
            stream_id=summarize_identifier_for_log(stream_id, keep=8),
            error_id=error_id,
            error=str(exc),
            error_category=error_category,
        )
        code = (
            model_error_code(error_category)
            if error_category in {"model_stream", "model_auth", "model_quota"}
            else "UPSTREAM_UNAVAILABLE"
            if error_category == "knowledge_base"
            else "INTERNAL_ERROR"
        )
        error_payload = _build_server_error_payload(
            message=error_message,
            error_id=error_id,
            trace_id=trace_id,
            code=code,
            retryable=error_category in {"model_stream", "knowledge_base"},
            details={"category": error_category},
        )
        yield encode_sse_event("server_error", error_payload, trace_id=trace_id)
    finally:
        if stream_id:
            await clear_stream_handle(app, stream_id)
        clear_namespace()
