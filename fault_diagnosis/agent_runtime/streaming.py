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
from ..diagnosis.artifact_store import save_thread_artifact
from ..diagnosis.contracts import DiagnosisArtifactEnvelope
from ..single_agent import RestrictedSingleAgentRunner
from ..agent_engine import AgentEngineV2, WorkflowRuntimeExecutor
from ..agent_engine.cutover import prepare_v2_execution_plan
from ..agent_engine.flags import is_legacy_rollback_enabled, load_agent_engine_flags
from ..agent_engine.output import project_start, project_task_update, project_token, project_tool_end, project_tool_start
from ..agent_engine.runtime import CancelToken
from ..security.contracts import AuthContext
from ..security.permissions import build_auth_context

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
    """聊天 SSE 兼容入口：dev mock、V2 默认主链路或 legacy rollback。"""

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

        flags = load_agent_engine_flags()
        if not is_legacy_rollback_enabled(flags=flags):
            effective_auth = auth_context or _fallback_auth_context(user_identity)
            v2_snapshot = AgentEngineV2().plan_only(
                raw_message=message,
                thread_id=thread_id,
                request_id=request_id,
                auth_context=effective_auth,
                conversation_context=conversation_context,
                metadata={"stream_id": stream_id, "source": "chat_stream"},
            )
            v2_plan = prepare_v2_execution_plan(snapshot=v2_snapshot, thread_id=thread_id)
            async for chunk in _stream_v2_runtime(
                app=app,
                plan=v2_plan,
                thread_id=thread_id,
                request_id=request_id,
                stream_id=stream_id,
                trace_id=trace_id,
                auth_context=effective_auth,
                cancel_handle=cancel_handle,
                complete_payload_enricher=complete_payload_enricher,
            ):
                yield chunk
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


async def _stream_v2_runtime(
    *,
    app: FastAPI,
    plan,
    thread_id: str,
    request_id: str,
    stream_id: str,
    trace_id: str,
    auth_context: AuthContext,
    cancel_handle: StreamCancellationHandle | None,
    complete_payload_enricher,
) -> AsyncGenerator[str, None]:
    yield encode_sse_event(
        "start",
        project_start(thread_id=thread_id, stream_id=stream_id, trace_id=trace_id),
        trace_id=trace_id,
    )
    token = CancelToken()
    if cancel_handle is not None and cancel_handle.cancel_event.is_set():
        token.cancel(cancel_handle.cancel_reason or "user_stop")
    state_for_start = _state_from_plan(
        plan=plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )
    yield encode_sse_event("task_update", project_task_update(state=state_for_start), trace_id=trace_id)
    for node in plan.nodes:
        yield encode_sse_event(
            "tool_start",
            project_tool_start(state=state_for_start, node=node),
            trace_id=trace_id,
        )
    executor = WorkflowRuntimeExecutor(
        real_tools=True,
        tool_runtime=getattr(app.state, "agent_engine_v2_tool_runtime", None),
    )
    result = executor.execute(
        plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        cancel_token=token,
        auth_context=auth_context,
    )
    state_for_progress = _state_from_result(
        plan=plan,
        result=result,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )
    yield encode_sse_event("task_update", project_task_update(state=state_for_progress), trace_id=trace_id)
    for node_result in result.node_results:
        yield encode_sse_event(
            "tool_end",
            project_tool_end(state=state_for_progress, result=node_result),
            trace_id=trace_id,
        )
    if result.output_frame.final_answer and not result.complete_payload.get("cancelled"):
        yield encode_sse_event("token", project_token(result.output_frame), trace_id=trace_id)
    complete = dict(result.complete_payload)
    if complete_payload_enricher is not None:
        try:
            complete = complete_payload_enricher(complete)
        except Exception as exc:  # noqa: BLE001
            _log.warning("V2 complete payload enrichment failed", thread_id=thread_id, error=str(exc))
    _save_v2_complete_artifact(complete, thread_id=thread_id)
    yield encode_sse_event("complete", complete, trace_id=trace_id)


def _state_from_plan(*, plan, trace_id: str, thread_id: str, request_id: str, auth_context: AuthContext):
    from ..agent_engine.runtime import RuntimeState

    return RuntimeState(
        plan=plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )


def _state_from_result(*, plan, result, trace_id: str, thread_id: str, request_id: str, auth_context: AuthContext):
    state = _state_from_plan(
        plan=plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )
    state.status = result.status
    state.node_results = list(result.node_results)
    state.evidence_ledger = result.evidence_ledger
    return state


def _fallback_auth_context(user_identity: str) -> AuthContext:
    role = "admin" if str(user_identity or "") == "管理员" else "guest"
    return build_auth_context(role=role)


def _save_v2_complete_artifact(complete: dict[str, Any], *, thread_id: str) -> None:
    artifact = complete.get("artifact")
    if not isinstance(artifact, dict) or complete.get("cancelled"):
        return
    try:
        envelope = DiagnosisArtifactEnvelope.model_validate(artifact)
        saved = save_thread_artifact(envelope)
        complete["artifact"] = saved.model_dump(mode="json", exclude_none=True)
    except Exception as exc:  # noqa: BLE001 - artifact save failure should not break streaming response.
        _log.warning("V2 artifact save failed", thread_id=thread_id, error=str(exc))
