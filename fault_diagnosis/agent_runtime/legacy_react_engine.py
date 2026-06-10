"""Legacy LangGraph ReAct 路径的 SSE 流执行引擎。"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, AsyncGenerator, Awaitable, Callable

from fastapi import FastAPI
from langchain_core.messages import HumanMessage, RemoveMessage

from ..config import (
    MODEL_STREAM_FIRST_EVENT_TIMEOUT_SECONDS,
    RECURSION_LIMIT,
    STREAM_HEARTBEAT_SECONDS,
)
from .error_classification import classify_model_gateway_error, model_error_code
from ..quality.governance import build_governance_snapshot
from ..common.logger import get_logger, new_request_id
from ..prompts.dynamic_prompt import Context
from ..runtime import (
    ExecutionRuntimeContext,
    build_tool_end_payload,
    build_tool_start_payload,
    build_workflow_stage_details,
    register_tool_runtime_evidence,
    resolve_tool_stage,
    touch_tool_stage_detail,
)
from .stream_control import StreamCancellationHandle
from ..common.utils import (
    parse_todos_from_tool_output,
    sanitize_for_json,
    summarize_identifier_for_log,
    summarize_text_for_log,
    summarize_value_for_log,
)
from ..workflows.adapters import save_legacy_diagnosis_artifact
from ..workflows.contracts import WorkflowType
from .event_contracts import (
    ChatStartEvent,
    PingEvent,
    TokenEvent,
    ToolProgressEvent,
    ToolStreamEvent,
)
from .sse_adapter import build_server_error_payload, encode_sse_event

try:
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
except Exception:
    REMOVE_ALL_MESSAGES = "__remove_all__"

_log = get_logger("agent_runtime.legacy_react_engine")

_SQL_PREFIX_RE = re.compile(r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|SHOW|DESCRIBE|EXPLAIN)\b", re.IGNORECASE)
_SQL_SIGNAL_RE = re.compile(r"\b(SELECT|FROM|WHERE|ORDER BY|GROUP BY|LIMIT|COUNT\s*\(|MIN\s*\(|MAX\s*\(|JOIN)\b", re.IGNORECASE)
_JSON_TOOL_HINT_RE = re.compile(r'^\s*[\[{].*("query"|"table_names"|"todos"|"status"|"content"|"tool")', re.IGNORECASE | re.DOTALL)
_CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def extract_content(message: Any) -> str:
    if hasattr(message, "content") and message.content:
        return str(message.content)
    if isinstance(message, dict) and message.get("content"):
        return str(message["content"])
    return ""


def extract_chunk_content(chunk: Any) -> str:
    if hasattr(chunk, "content") and chunk.content:
        return str(chunk.content)
    if isinstance(chunk, dict) and chunk.get("content"):
        return str(chunk["content"])
    return ""


def chunk_has_tool_call_metadata(chunk: Any) -> bool:
    if chunk is None:
        return False

    for attr in ("tool_call_chunks", "tool_calls", "invalid_tool_calls"):
        value = getattr(chunk, attr, None)
        if isinstance(value, (list, tuple, dict, str)) and value:
            return True

    if isinstance(chunk, dict):
        if chunk.get("tool_call_chunks") or chunk.get("tool_calls") or chunk.get("invalid_tool_calls"):
            return True
        additional_kwargs = chunk.get("additional_kwargs") or {}
        if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
            return True

    return False


def looks_like_internal_tooling_text(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if not normalized:
        return False

    if normalized.startswith("Command(update="):
        return True

    if _JSON_TOOL_HINT_RE.match(normalized):
        return True

    if _SQL_PREFIX_RE.match(normalized):
        return True

    sql_hits = len(_SQL_SIGNAL_RE.findall(normalized))
    if sql_hits >= 3 and not _CHINESE_CHAR_RE.search(normalized):
        return True

    return False


def looks_like_user_visible_text(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if not normalized:
        return False

    if looks_like_internal_tooling_text(normalized):
        return False

    if _CHINESE_CHAR_RE.search(normalized):
        return True

    if re.search(r"[A-Za-z]", normalized) and len(normalized) >= 6:
        return True

    return False


def extract_result_payload(result: Any) -> tuple[str, list]:
    if isinstance(result, dict):
        todos = result.get("todos") or []
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            return extract_content(messages[-1]), todos
        output = result.get("output")
        if isinstance(output, dict):
            output_todos = output.get("todos") or todos
            output_messages = output.get("messages")
            if isinstance(output_messages, list) and output_messages:
                return extract_content(output_messages[-1]), output_todos
        return "", todos
    return extract_content(result), []


def classify_stream_error(error: Exception) -> tuple[str, str]:
    error_text = str(error)
    lowered = error_text.lower()
    error_name = error.__class__.__name__

    if "Recursion limit" in error_text or "GRAPH_RECURSION_LIMIT" in error_text:
        return "recursion_limit", "本次思考过长，请回复继续以继续思考"
    model_gateway_error = classify_model_gateway_error(error)
    if model_gateway_error:
        return model_gateway_error
    if "知识库" in error_text or "faiss" in lowered or "ollama" in lowered or "embedding" in lowered:
        return "knowledge_base", "知识库当前不可用，请先确认已完成预构建或稍后重试"
    if "during streaming" in lowered or "stream" in lowered or error_name in {
        "APIError",
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }:
        return "model_stream", "上游模型流式输出中断，请稍后重试"
    if "tool" in lowered:
        return "tool_execution", "工具执行失败，请稍后重试"
    return "internal", "请求处理失败，请稍后重试"


async def close_async_iter(async_iter: Any) -> None:
    close_fn = getattr(async_iter, "aclose", None)
    if close_fn is None:
        return
    try:
        close_result = close_fn()
        if asyncio.iscoroutine(close_result):
            await close_result
    except RuntimeError:
        # 客户端断开时 async generator 可能仍在运行，安全忽略即可。
        pass


async def emit_non_stream_fallback(
    app: FastAPI,
    inputs: dict,
    config: dict,
    context: Context,
) -> tuple[str, list]:
    result = await app.state.agent.ainvoke(inputs, config=config, context=context)
    final_content, todos = extract_result_payload(result)
    return final_content, todos


class LegacyReactStreamEngine:
    """封装 legacy LangGraph ReAct 流式执行路径。"""

    def __init__(
        self,
        *,
        diagnosis_payload_builder: Callable[[str], dict[str, Any]],
        auto_evidence_selector: Callable[[dict[str, Any] | None], str | None],
        auto_evidence_runner: Callable[[str, str, str], Awaitable[dict[str, Any] | None]],
        logger: Any | None = None,
    ) -> None:
        self.diagnosis_payload_builder = diagnosis_payload_builder
        self.auto_evidence_selector = auto_evidence_selector
        self.auto_evidence_runner = auto_evidence_runner
        self._log = logger or _log

    async def stream(
        self,
        app: FastAPI,
        message: str,
        thread_id: str,
        user_identity: str,
        *,
        request_id: str,
        stream_id: str,
        trace_id: str,
        cancel_handle: StreamCancellationHandle | None = None,
        history_messages: list[Any] | None = None,
        replace_history: bool = False,
    ) -> AsyncGenerator[str, None]:
        """执行 legacy ReAct 流并输出兼容 SSE 帧。"""

        cancel_event = cancel_handle.cancel_event if cancel_handle else None
        pending_event_task: asyncio.Task | None = None
        agent_stream = None
        stream_cancelled = False
        cancel_reason = None
        try:
            yield encode_sse_event(
                "start",
                ChatStartEvent(
                    thread_id=thread_id,
                    stream_id=stream_id,
                    trace_id=trace_id,
                    stage="reasoning",
                    message="模型已开始推理，等待首个可显示 token...",
                ),
            )

            input_messages: list[Any] = []
            if replace_history:
                input_messages.append(RemoveMessage(id=REMOVE_ALL_MESSAGES))
                input_messages.extend(history_messages or [])
            input_messages.append(HumanMessage(content=message))
            inputs = {"messages": input_messages}
            context = Context(user_identity=user_identity)
            config = {
                "configurable": {
                    "thread_id": thread_id,
                },
                "recursion_limit": RECURSION_LIMIT,
            }

            accumulated_content = ""
            event_count = 0
            token_count = 0
            tool_event_count = 0
            current_todos = []
            pending_internal_buffer = ""
            stream_started_at = time.monotonic()
            first_event_ms: float | None = None
            first_token_ms: float | None = None
            used_non_stream_fallback = False
            runtime_context = ExecutionRuntimeContext(stream_started_at=stream_started_at)

            self._log.info(
                "流式会话开始",
                thread_id=summarize_identifier_for_log(thread_id, keep=10),
                stream_id=summarize_identifier_for_log(stream_id, keep=8),
                user_identity=user_identity,
                message_len=len(message),
                message_preview=summarize_text_for_log(message, limit=72),
            )

            agent_stream = app.state.agent.astream_events(
                inputs,
                config=config,
                context=context,
                version="v2",
                stream_mode="messages",
            )

            while True:
                try:
                    if cancel_event is not None and cancel_event.is_set():
                        cancel_reason = cancel_handle.cancel_reason if cancel_handle else "cancelled"
                        stream_cancelled = True
                        self._log.info(
                            "检测到流式取消信号，准备结束请求",
                            thread_id=summarize_identifier_for_log(thread_id, keep=10),
                            stream_id=summarize_identifier_for_log(stream_id, keep=8),
                            cancel_reason=cancel_reason,
                            event_count=event_count,
                            token_count=token_count,
                            tool_event_count=tool_event_count,
                        )
                        break

                    if pending_event_task is None:
                        pending_event_task = asyncio.create_task(anext(agent_stream))

                    cancel_wait_task: asyncio.Task | None = None
                    wait_tasks = {pending_event_task}
                    if cancel_event is not None and not cancel_event.is_set():
                        cancel_wait_task = asyncio.create_task(cancel_event.wait())
                        wait_tasks.add(cancel_wait_task)

                    done, _ = await asyncio.wait(
                        wait_tasks,
                        timeout=STREAM_HEARTBEAT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if cancel_wait_task is not None:
                        if cancel_wait_task in done:
                            cancel_reason = cancel_handle.cancel_reason if cancel_handle else "cancelled"
                            stream_cancelled = True
                            self._log.info(
                                "流式请求收到取消信号",
                                thread_id=summarize_identifier_for_log(thread_id, keep=10),
                                stream_id=summarize_identifier_for_log(stream_id, keep=8),
                                cancel_reason=cancel_reason,
                                event_count=event_count,
                                token_count=token_count,
                                tool_event_count=tool_event_count,
                            )
                            if pending_event_task is not None and not pending_event_task.done():
                                pending_event_task.cancel()
                                try:
                                    await pending_event_task
                                except BaseException:
                                    pass
                            pending_event_task = None
                            await close_async_iter(agent_stream)
                            break
                        if not cancel_wait_task.done():
                            cancel_wait_task.cancel()

                    if not done:
                        if event_count == 0 and token_count == 0 and tool_event_count == 0:
                            first_event_wait = time.monotonic() - stream_started_at
                            if first_event_wait >= MODEL_STREAM_FIRST_EVENT_TIMEOUT_SECONDS:
                                self._log.warning(
                                    "模型流式首事件超时，切换为非流式回退",
                                    thread_id=thread_id,
                                    wait_seconds=round(first_event_wait, 2),
                                    request_id=request_id,
                                )
                                pending_event_task.cancel()
                                pending_event_task = None
                                await close_async_iter(agent_stream)
                                accumulated_content, current_todos = await emit_non_stream_fallback(
                                    app,
                                    inputs,
                                    config,
                                    context,
                                )
                                used_non_stream_fallback = True
                                if accumulated_content:
                                    token_count += 1
                                    yield encode_sse_event("token", TokenEvent(content=accumulated_content))
                                break
                        ping_stage = "reasoning" if token_count == 0 and tool_event_count == 0 else "keepalive"
                        ping_message = (
                            "模型仍在推理，尚未产出可显示内容..."
                            if ping_stage == "reasoning"
                            else "长任务处理中，连接保持中..."
                        )
                        yield encode_sse_event(
                            "ping",
                            PingEvent(
                                timestamp=datetime.now().isoformat(),
                                trace_id=trace_id,
                                stage=ping_stage,
                                message=ping_message,
                            ),
                        )
                        continue

                    try:
                        event = pending_event_task.result()
                    except StopAsyncIteration:
                        pending_event_task = None
                        break
                    except Exception as stream_error:
                        pending_event_task = None
                        if token_count == 0 and tool_event_count == 0:
                            error_category, _ = classify_stream_error(stream_error)
                            if error_category == "model_auth":
                                raise
                            self._log.warning(
                                "流式阶段在产生首个有效事件前失败，尝试非流式回退",
                                thread_id=thread_id,
                                request_id=request_id,
                                error=str(stream_error),
                            )
                            await close_async_iter(agent_stream)
                            accumulated_content, current_todos = await emit_non_stream_fallback(
                                app,
                                inputs,
                                config,
                                context,
                            )
                            used_non_stream_fallback = True
                            if accumulated_content:
                                token_count += 1
                                yield encode_sse_event("token", TokenEvent(content=accumulated_content))
                            break
                        raise
                    pending_event_task = None
                except StopAsyncIteration:
                    break

                event_count += 1
                event_type = event.get("event", "")
                if first_event_ms is None:
                    first_event_ms = round((time.monotonic() - stream_started_at) * 1000, 1)
                    self._log.info(
                        "收到首个模型事件",
                        thread_id=summarize_identifier_for_log(thread_id, keep=10),
                        stream_id=summarize_identifier_for_log(stream_id, keep=8),
                        event_type=event_type,
                        first_event_ms=first_event_ms,
                    )

                if event_type == "on_chat_model_stream":
                    chunk = event["data"].get("chunk", {})
                    if chunk_has_tool_call_metadata(chunk):
                        continue

                    token_content = extract_chunk_content(chunk)
                    if token_content:
                        if token_count == 0:
                            pending_internal_buffer += token_content
                            normalized_pending = pending_internal_buffer.strip()
                            if not normalized_pending:
                                continue
                            if looks_like_internal_tooling_text(normalized_pending):
                                continue
                            if not looks_like_user_visible_text(normalized_pending) and len(normalized_pending) < 48:
                                continue
                            token_content = pending_internal_buffer
                            pending_internal_buffer = ""

                        accumulated_content += token_content
                        token_count += 1
                        if token_count == 1:
                            first_token_ms = round((time.monotonic() - stream_started_at) * 1000, 1)
                            self._log.info(
                                "收到首个有效 token",
                                thread_id=summarize_identifier_for_log(thread_id, keep=10),
                                stream_id=summarize_identifier_for_log(stream_id, keep=8),
                                first_token_ms=first_token_ms,
                            )
                        yield encode_sse_event("token", TokenEvent(content=token_content))

                elif event_type.startswith("on_tool"):
                    tool_name = event.get("name", "")
                    tool_event_count += 1

                    if event_type == "on_tool_start":
                        if pending_internal_buffer and looks_like_internal_tooling_text(pending_internal_buffer):
                            pending_internal_buffer = ""
                        tool_input_raw = event["data"].get("input", {})
                        tool_input = sanitize_for_json(tool_input_raw)
                        tool_run_id = str(event.get("run_id") or f"{tool_name}-{tool_event_count}")
                        tool_stage = runtime_context.handle_tool_start(
                            tool_name=tool_name,
                            tool_run_id=tool_run_id,
                            tool_input=tool_input_raw,
                            now=time.monotonic(),
                            tool_input_preview=tool_input,
                        )
                        self._log.info(
                            "工具开始",
                            thread_id=summarize_identifier_for_log(thread_id, keep=10),
                            stream_id=summarize_identifier_for_log(stream_id, keep=8),
                            tool_name=tool_name,
                            tool_run_id=summarize_identifier_for_log(tool_run_id, keep=6),
                            input_preview=summarize_value_for_log(tool_input_raw, limit=160),
                        )
                        tool_start_payload = build_tool_start_payload(
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_stage=tool_stage,
                            current_workflow_stage=runtime_context.current_workflow_stage,
                            tool_run_id=tool_run_id,
                            trace_id=trace_id,
                        )
                        yield encode_sse_event("tool_start", tool_start_payload, trace_id=trace_id)
                        progress_payload = ToolProgressEvent(
                            trace_id=trace_id,
                            run_id=tool_run_id,
                            tool_name=tool_name,
                            stage=tool_stage,
                            message="工具已开始执行",
                            progress=0.0,
                            metadata={
                                "current_stage": runtime_context.current_workflow_stage,
                            },
                        )
                        yield encode_sse_event("tool_progress", progress_payload, trace_id=trace_id)

                    elif event_type == "on_tool_end":
                        tool_run_id = str(
                            event.get("run_id")
                            or runtime_context.find_pending_tool_run_id(tool_name)
                            or f"{tool_name}-{tool_event_count}"
                        )
                        tool_stage = resolve_tool_stage(tool_name)
                        tool_raw_output = event["data"].get("output", "")
                        tool_output = sanitize_for_json(tool_raw_output)
                        tool_input_raw, started_at = runtime_context.pop_tool_run(tool_run_id)
                        tool_result_preview = summarize_value_for_log(tool_raw_output, limit=180)
                        tool_evidence = register_tool_runtime_evidence(
                            tool_name=tool_name,
                            tool_input=tool_input_raw,
                            tool_output=tool_raw_output,
                        )
                        duration_ms = round((time.monotonic() - started_at) * 1000, 1) if started_at else None
                        now_time = time.monotonic()
                        stage_now_ms = runtime_context.elapsed_ms(now_time)
                        stage_detail = touch_tool_stage_detail(
                            runtime_context.workflow_stage_details,
                            tool_stage,
                            now_ms=stage_now_ms,
                        )
                        runtime_context.record_tool_end(
                            tool_name=tool_name,
                            tool_run_id=tool_run_id,
                            tool_stage=tool_stage,
                            now=now_time,
                            duration_ms=duration_ms,
                            result_preview=tool_result_preview,
                            evidence_ids=[
                                item.get("evidence_id")
                                for item in tool_evidence
                                if isinstance(item, dict) and item.get("evidence_id")
                            ],
                        )
                        payload = build_tool_end_payload(
                            tool_name=tool_name,
                            tool_stage=tool_stage,
                            current_workflow_stage=runtime_context.current_workflow_stage,
                            tool_output=tool_output,
                            tool_run_id=tool_run_id,
                            trace_id=trace_id,
                            stage_duration_ms=stage_detail.get("duration_ms"),
                            tool_evidence=tool_evidence,
                        )
                        yield encode_sse_event("tool_end", payload, trace_id=trace_id)
                        progress_payload = ToolProgressEvent(
                            trace_id=trace_id,
                            run_id=tool_run_id,
                            tool_name=tool_name,
                            stage=tool_stage,
                            message="工具执行完成",
                            progress=1.0,
                            metadata={
                                "current_stage": runtime_context.current_workflow_stage,
                                "duration_ms": duration_ms,
                            },
                        )
                        yield encode_sse_event("tool_progress", progress_payload, trace_id=trace_id)
                        stream_chunk = tool_output if isinstance(tool_output, str) else tool_result_preview
                        if stream_chunk:
                            stream_payload = ToolStreamEvent(
                                trace_id=trace_id,
                                run_id=tool_run_id,
                                tool_name=tool_name,
                                chunk=str(stream_chunk),
                                done=True,
                                metadata={
                                    "stage": tool_stage,
                                },
                            )
                            yield encode_sse_event("tool_stream", stream_payload, trace_id=trace_id)

                        if tool_name == "write_todos":
                            try:
                                parsed_todos = parse_todos_from_tool_output(tool_raw_output) or parse_todos_from_tool_output(tool_output)
                                if parsed_todos:
                                    current_todos = parsed_todos
                            except Exception as exc:
                                self._log.warning("解析todos失败", error=str(exc))

                        self._log.info(
                            "工具完成",
                            thread_id=summarize_identifier_for_log(thread_id, keep=10),
                            stream_id=summarize_identifier_for_log(stream_id, keep=8),
                            tool_name=tool_name,
                            tool_run_id=summarize_identifier_for_log(tool_run_id, keep=6),
                            duration_ms=duration_ms,
                            todo_count=len(current_todos),
                            result_preview=tool_result_preview,
                        )

                elif event_type == "on_chain_end" and event.get("name") == "LangGraph":
                    output = (event.get("data") or {}).get("output", {})
                    if isinstance(output, dict):
                        if "todos" in output:
                            current_todos = output["todos"]
                        if "messages" in output:
                            messages = output["messages"]
                            try:
                                if messages and hasattr(messages[-1], "content"):
                                    accumulated_content = messages[-1].content
                            except Exception:
                                pass

            if stream_cancelled:
                self._log.info(
                    "流式请求已根据取消信号提前结束",
                    thread_id=summarize_identifier_for_log(thread_id, keep=10),
                    stream_id=summarize_identifier_for_log(stream_id, keep=8),
                    cancel_reason=cancel_reason,
                    event_count=event_count,
                    token_count=token_count,
                    tool_event_count=tool_event_count,
                    duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
                )
                return

            if token_count == 0:
                fallback_token = pending_internal_buffer.strip() or accumulated_content.strip()
                if fallback_token and not looks_like_internal_tooling_text(fallback_token):
                    accumulated_content = fallback_token
                    token_count = 1
                    self._log.info(
                        "流阶段无可显示 token，完成前补发最终内容",
                        thread_id=summarize_identifier_for_log(thread_id, keep=10),
                        stream_id=summarize_identifier_for_log(stream_id, keep=8),
                        final_token_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
                    )
                    yield encode_sse_event("token", TokenEvent(content=fallback_token))

            runtime_context.finalize_workflow(time.monotonic())
            diagnosis_payload = self.diagnosis_payload_builder(accumulated_content)
            supplement_tool = self.auto_evidence_selector(diagnosis_payload["evidence_quality"])
            if supplement_tool:
                supplement_result = await self.auto_evidence_runner(
                    message,
                    user_identity,
                    supplement_tool,
                )
                if supplement_result:
                    tool_name = str(supplement_result.get("tool_name") or supplement_tool)
                    tool_input_raw = supplement_result.get("tool_input") or {}
                    tool_run_id = f"auto-supplement-{tool_name}-{tool_event_count + 1}"
                    tool_stage = runtime_context.handle_tool_start(
                        tool_name=tool_name,
                        tool_run_id=tool_run_id,
                        tool_input=tool_input_raw,
                        now=time.monotonic(),
                    )
                    tool_start_payload = build_tool_start_payload(
                        tool_name=tool_name,
                        tool_input=sanitize_for_json(tool_input_raw),
                        tool_stage=tool_stage,
                        current_workflow_stage=runtime_context.current_workflow_stage,
                        tool_run_id=tool_run_id,
                    )
                    yield encode_sse_event("tool_start", tool_start_payload, trace_id=trace_id)
                    tool_event_count += 1

                    tool_raw_output = supplement_result.get("tool_output", "")
                    tool_evidence = register_tool_runtime_evidence(
                        tool_name=tool_name,
                        tool_input=tool_input_raw,
                        tool_output=tool_raw_output,
                    )
                    now_time = time.monotonic()
                    stage_now_ms = runtime_context.elapsed_ms(now_time)
                    stage_detail = touch_tool_stage_detail(
                        runtime_context.workflow_stage_details,
                        tool_stage,
                        now_ms=stage_now_ms,
                    )
                    runtime_context.record_tool_end(
                        tool_name=tool_name,
                        tool_run_id=tool_run_id,
                        tool_stage=tool_stage,
                        now=now_time,
                        duration_ms=0.0,
                        result_preview=summarize_value_for_log(tool_raw_output, limit=180),
                        evidence_ids=[
                            item.get("evidence_id")
                            for item in tool_evidence
                            if isinstance(item, dict) and item.get("evidence_id")
                        ],
                    )
                    tool_end_payload = build_tool_end_payload(
                        tool_name=tool_name,
                        tool_stage=tool_stage,
                        current_workflow_stage=runtime_context.current_workflow_stage,
                        tool_output=sanitize_for_json(tool_raw_output),
                        stage_duration_ms=stage_detail.get("duration_ms"),
                        tool_evidence=tool_evidence,
                    )
                    yield encode_sse_event("tool_end", tool_end_payload, trace_id=trace_id)
                    diagnosis_payload = self.diagnosis_payload_builder(accumulated_content)

            if used_non_stream_fallback and event_count == 0 and tool_event_count == 0:
                diagnosis_payload["raw_final_content"] = accumulated_content
                diagnosis_payload["final_content"] = accumulated_content
                diagnosis_payload["grounded_final_content"] = accumulated_content
                diagnosis_payload["quality_gate_notice"] = None

            linked_tool_lifecycle_ledger = runtime_context.enrich_lifecycle_with_findings(
                diagnosis_payload["finding_links"],
            )
            route_result = {
                "workflow_type": WorkflowType.FAULT_DIAGNOSIS.value,
                "confidence": "high",
                "reason": "legacy 主链路已实际执行故障诊断流程",
                "needs_sql": True,
                "needs_knowledge": True,
                "needs_report": True,
            }
            governance_snapshot = build_governance_snapshot(
                route_result=route_result,
                evidence_quality=diagnosis_payload["evidence_quality"],
                findings=diagnosis_payload["findings"],
            )
            completion_data = {
                "type": "chat_complete",
                "thread_id": thread_id,
                "trace_id": trace_id,
                "raw_final_content": diagnosis_payload["raw_final_content"],
                "final_content": diagnosis_payload["final_content"],
                "grounded_final_content": diagnosis_payload["grounded_final_content"],
                "todos": current_todos,
                "event_count": event_count,
                "evidence_count": len(diagnosis_payload["evidence_records"]),
                "evidences": diagnosis_payload["evidence_records"],
                "normalized_evidences": diagnosis_payload["normalized_evidence_records"],
                "findings": diagnosis_payload["findings"],
                "finding_links": diagnosis_payload["finding_links"],
                "evidence_quality": diagnosis_payload["evidence_quality"],
                "governance": governance_snapshot,
                "evidence_coverage": diagnosis_payload["evidence_quality"].get("coverage_summary"),
                "report_gate": diagnosis_payload["evidence_quality"].get("gate"),
                "quality_gate_notice": diagnosis_payload["quality_gate_notice"],
                "release_ready": diagnosis_payload["evidence_quality"].get("release_ready"),
                "workflow_stages": runtime_context.workflow_stages_seen,
                "current_stage": runtime_context.current_workflow_stage,
                "workflow_stage_details": build_workflow_stage_details(
                    runtime_context.workflow_stages_seen,
                    runtime_context.workflow_stage_details,
                ),
                "tool_lifecycle_ledger": linked_tool_lifecycle_ledger,
                "timestamp": datetime.now().isoformat(),
            }
            save_legacy_diagnosis_artifact(
                thread_id=thread_id,
                user_message=message,
                user_identity=user_identity,
                final_content=diagnosis_payload["raw_final_content"],
                findings=diagnosis_payload["findings"],
                evidence_records=diagnosis_payload["evidence_records"],
                evidence_quality=diagnosis_payload["evidence_quality"],
                finding_links=diagnosis_payload["finding_links"],
                workflow_stage_details=completion_data["workflow_stage_details"],
                route_result=route_result,
            )
            yield encode_sse_event("complete", completion_data, trace_id=trace_id)
            self._log.info(
                "流式请求完成",
                thread_id=summarize_identifier_for_log(thread_id, keep=10),
                stream_id=summarize_identifier_for_log(stream_id, keep=8),
                event_count=event_count,
                token_count=token_count,
                tool_event_count=tool_event_count,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
                first_event_ms=first_event_ms,
                first_token_ms=first_token_ms,
                todo_count=len(current_todos),
            )

        except asyncio.CancelledError:
            self._log.warning(
                "流式请求被取消",
                thread_id=summarize_identifier_for_log(thread_id, keep=10),
                stream_id=summarize_identifier_for_log(stream_id, keep=8),
                cancel_reason=cancel_reason,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1) if "stream_started_at" in locals() else None,
            )
            return
        except Exception as exc:
            error_id = request_id or new_request_id()
            error_category, error_message = classify_stream_error(exc)
            self._log.exception(
                "Token流式处理错误",
                thread_id=summarize_identifier_for_log(thread_id, keep=10),
                stream_id=summarize_identifier_for_log(stream_id, keep=8),
                error_id=error_id,
                error=str(exc),
                error_category=error_category,
                event_count=event_count if "event_count" in locals() else 0,
                token_count=token_count if "token_count" in locals() else 0,
                tool_event_count=tool_event_count if "tool_event_count" in locals() else 0,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1) if "stream_started_at" in locals() else None,
            )

            code = (
                model_error_code(error_category)
                if error_category in {"model_stream", "model_auth", "model_quota"}
                else "UPSTREAM_UNAVAILABLE"
                if error_category == "knowledge_base"
                else "INTERNAL_ERROR"
            )
            error_payload = build_server_error_payload(
                message=error_message,
                error_id=error_id,
                trace_id=trace_id,
                code=code,
                retryable=error_category in {"model_stream", "knowledge_base"},
                details={"category": error_category},
            )
            yield encode_sse_event("server_error", error_payload, trace_id=trace_id)
        finally:
            if pending_event_task is not None and not pending_event_task.done():
                pending_event_task.cancel()
            if agent_stream is not None:
                await close_async_iter(agent_stream)
