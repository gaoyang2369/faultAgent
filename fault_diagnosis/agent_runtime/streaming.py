"""聊天 SSE 兼容入口。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from fastapi import FastAPI

from .legacy_react_engine import LegacyReactStreamEngine, classify_stream_error
from .sse_adapter import (
    adapt_sse_chunk,
    build_server_error_payload,
    build_trace_id,
    encode_sse_event,
    enrich_complete_payload,
    enrich_workflow_sse_chunk,
    event_payload_with_type,
)
from .workflow_engine import WorkflowStreamEngine
from ..config import ENABLE_WORKFLOW_V1
from ..runtime.dev_mode import stream_dev_chat_events
from .error_classification import model_error_code
from ..common.logger import bind_request_id, get_logger, new_request_id
from ..runtime import build_diagnosis_runtime_payload
from ..runtime.session_store import clear_namespace, set_namespace
from .stream_control import StreamCancellationHandle, clear_stream_handle
from ..common.utils import summarize_identifier_for_log
from ..workflows.artifact_store import get_thread_artifact
from ..workflows.contracts import WorkflowType
from ..workflows.router import route_workflow_request
from ..workflows.runner import stream_workflow_events

_log = get_logger("streaming")

_REPORT_HANDOFF_CONTEXT_HINTS = (
    "刚才",
    "刚刚",
    "上一轮",
    "上一条",
    "上一次",
    "前面的结果",
    "刚才结果",
    "诊断结果",
    "巡检结果",
)


def _should_use_workflow_report_generation(message: str, thread_id: str, user_identity: str) -> bool:
    normalized_message = (message or "").strip()
    if not any(hint in normalized_message for hint in _REPORT_HANDOFF_CONTEXT_HINTS):
        return False
    route_result = route_workflow_request(message, user_identity)
    workflow_type = str(route_result.workflow_type)
    if workflow_type != WorkflowType.REPORT_GENERATION.value:
        return False
    envelope = get_thread_artifact(thread_id)
    if envelope is None:
        return False
    return str(envelope.workflow_type) in {
        WorkflowType.FAULT_DIAGNOSIS.value,
        WorkflowType.STATUS_INSPECTION.value,
    }


def _should_use_workflow_mainline(app: FastAPI, *, replace_history: bool) -> bool:
    """判断普通聊天请求是否应进入 Workflow V1 主链路。"""

    if replace_history:
        return False
    if getattr(app.state, "dev_mode", False):
        return False
    return bool(ENABLE_WORKFLOW_V1)


def _merge_phase4_contract_payload(data: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """为 Workflow V1 complete 事件补充第四阶段结构化字段。"""

    return enrich_complete_payload(data, thread_id)


def _enrich_workflow_sse_chunk(chunk: str, thread_id: str) -> str:
    """兼容增强 Workflow SSE 帧，不改变既有事件外壳。"""

    return enrich_workflow_sse_chunk(chunk, thread_id)


def _build_trace_id(request_id: str) -> str:
    """为 SSE 会话构造稳定 trace 标识。"""

    return build_trace_id(request_id)


def _event_payload_with_type(payload: dict[str, Any], event_type_key: str = "event_type") -> dict[str, Any]:
    """补齐前端事件通用的 type 字段。"""

    return event_payload_with_type(payload, event_type_key)


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


def _inject_trace_into_sse_chunk(chunk: str, trace_id: str, *, thread_id: str) -> str:
    """为 Workflow / Dev 路径的既有 SSE 帧补充 trace_id。"""

    return adapt_sse_chunk(chunk, trace_id, thread_id=thread_id)


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
) -> AsyncGenerator[str, None]:
    """聊天 SSE 兼容入口，按运行路径委派给对应 engine。"""

    request_id = bind_request_id(request_id or new_request_id())
    trace_id = _build_trace_id(request_id)
    stream_id = (stream_id or "").strip()
    set_namespace({"__builtins__": __builtins__})

    workflow_engine = WorkflowStreamEngine(
        workflow_streamer=stream_workflow_events,
        dev_streamer=stream_dev_chat_events,
    )
    legacy_engine = LegacyReactStreamEngine(
        diagnosis_payload_builder=build_diagnosis_runtime_payload,
        logger=_log,
    )

    try:
        if not replace_history and _should_use_workflow_report_generation(message, thread_id, user_identity):
            async for chunk in workflow_engine.stream_workflow(
                app,
                message,
                thread_id,
                user_identity,
                request_id=request_id,
                stream_id=stream_id,
                trace_id=trace_id,
                cancel_handle=cancel_handle,
            ):
                yield chunk
            return

        if _should_use_workflow_mainline(app, replace_history=replace_history):
            async for chunk in workflow_engine.stream_workflow(
                app,
                message,
                thread_id,
                user_identity,
                request_id=request_id,
                stream_id=stream_id,
                trace_id=trace_id,
                cancel_handle=cancel_handle,
            ):
                yield chunk
            return

        if getattr(app.state, "dev_mode", False):
            cancel_event = cancel_handle.cancel_event if cancel_handle else None
            async for chunk in workflow_engine.stream_dev(
                app,
                message,
                thread_id,
                user_identity,
                trace_id=trace_id,
                cancel_event=cancel_event,
            ):
                yield chunk
            return

        async for chunk in legacy_engine.stream(
            app,
            message,
            thread_id,
            user_identity,
            request_id=request_id,
            stream_id=stream_id,
            trace_id=trace_id,
            cancel_handle=cancel_handle,
            history_messages=history_messages,
            replace_history=replace_history,
        ):
            yield chunk
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
