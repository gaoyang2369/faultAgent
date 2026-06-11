"""Restricted single-agent runner for the minimal diagnosis path."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import datetime
from typing import Any, AsyncGenerator

from ..agent_runtime.error_classification import classify_model_gateway_error, model_error_code
from ..agent_runtime.sse_adapter import build_server_error_payload, encode_sse_event
from ..common.logger import get_logger
from ..observability import NoopTraceRun, TraceRunContext, get_trace_exporter, write_local_trace
from ..diagnosis.adapters import invoke_tool
from .contracts import AgentTrace, SingleAgentLimits
from .errors import SingleAgentExecutionError
from .flow import SingleAgentFlowMixin
from .json_utils import build_json_repair_prompt, extract_json_text, loads_json_object
from .stages import SingleAgentStagesMixin
from .serialization import preview, sanitize_for_json, stringify

_log = get_logger("single_agent.runner")


class RestrictedSingleAgentRunner(SingleAgentStagesMixin, SingleAgentFlowMixin):
    """A deterministic, bounded single-agent diagnosis pipeline."""

    def __init__(
        self,
        *,
        message: str,
        thread_id: str,
        user_identity: str,
        request_id: str,
        stream_id: str,
        trace_id: str,
        limits: SingleAgentLimits | None = None,
        model: Any | None = None,
    ) -> None:
        self.message = message
        self.thread_id = thread_id
        self.user_identity = user_identity
        self.request_id = request_id
        self.stream_id = (stream_id or "").strip()
        self.trace_id = trace_id
        self.limits = limits or SingleAgentLimits()
        self.trace = AgentTrace(
            trace_id=trace_id,
            request_id=request_id,
            thread_id=thread_id,
            user_identity=user_identity,
            user_message=message,
            limits=self.limits,
        )
        self.model = model
        self.trace_exporter = get_trace_exporter()
        self._trace_run = NoopTraceRun(trace_context=self._build_trace_context())
        self._trace_finalized = False
        self._stage_observations: dict[str, list[Any]] = {}
        self.cancel_handle: Any = None
        self._round_count = 0
        self._tool_call_count = 0
        self._last_step_result: Any = None

    def _resolve_model(self):
        if self.model is not None:
            return self.model
        from langchain_openai import ChatOpenAI

        self.model = ChatOpenAI(
            model=os.getenv("MODEL_NAME"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.2,
        )
        return self.model

    def _is_cancelled(self) -> bool:
        event = getattr(self.cancel_handle, "cancel_event", None)
        return bool(event is not None and event.is_set())

    def _cancel_reason(self) -> str:
        return getattr(self.cancel_handle, "cancel_reason", None) or "cancelled"

    def _build_trace_context(self) -> TraceRunContext:
        return TraceRunContext(
            trace_id=self.trace_id,
            request_id=self.request_id,
            thread_id=self.thread_id,
            user_identity=self.user_identity,
            user_message=self.message,
            stream_id=self.stream_id,
            runtime="restricted_single_agent",
            model_name=os.getenv("MODEL_NAME"),
        )

    def _reset_trace_run(self) -> None:
        self._trace_run = NoopTraceRun(trace_context=self._build_trace_context())
        self._trace_finalized = False
        self._stage_observations = {}

    def _finalize_trace_run(
        self,
        *,
        status: str,
        final_answer: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._trace_finalized:
            return
        if self.trace.status == "running" or not self.trace.finished_at:
            self.trace.finish(status=status, final_answer=final_answer, error=error)
        trace_metadata = {
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "trace_id": self.trace_id,
            "stream_id": self.stream_id,
            "round_count": self._round_count,
            "tool_call_count": self._tool_call_count,
        }
        if metadata:
            trace_metadata.update(metadata)
        try:
            self._trace_run.finish(
                status=status,
                output=final_answer,
                error=error,
                metadata=trace_metadata,
            )
        except Exception as exc:  # pragma: no cover - export is best effort
            _log.warning("trace exporter 收口失败", error=str(exc), trace_id=self.trace_id)
        local_trace_path = write_local_trace(
            self.trace.model_dump(exclude_none=True),
            metadata=trace_metadata,
        )
        if local_trace_path:
            _log.info(
                "本地 trace 已写入",
                trace_id=self.trace_id,
                request_id=self.request_id,
                path=local_trace_path,
                event_count=len(self.trace.events),
            )
        self._trace_finalized = True

    def _finish_open_stage_observations(self, *, status: str, error: str | None = None) -> None:
        for stage, observations in list(self._stage_observations.items()):
            while observations:
                observation = observations.pop()
                try:
                    observation.finish(status=status, error=error)
                except Exception as exc:  # pragma: no cover - best effort cleanup
                    _log.warning("阶段 trace 关闭失败", stage=stage, error=str(exc))
        self._stage_observations.clear()

    def _start_stage(self, stage: str, message: str) -> float:
        self._round_count += 1
        if self._round_count > self.limits.max_rounds:
            raise SingleAgentExecutionError(f"超过单 Agent 最大阶段轮次限制：{self.limits.max_rounds}")
        self.trace.add_event("stage", stage=stage, status="started", message=message)
        observation = self._trace_run.start_observation(
            name=f"single_agent.stage.{stage}",
            as_type="span",
            input={"stage": stage, "message": message, "round": self._round_count},
            metadata={
                "stage": stage,
                "round": self._round_count,
                "thread_id": self.thread_id,
                "trace_id": self.trace_id,
            },
        )
        self._stage_observations.setdefault(stage, []).append(observation)
        return time.monotonic()

    def _finish_stage(
        self,
        stage: str,
        started_at: float,
        *,
        status: str = "completed",
        message: str = "",
        error: str | None = None,
    ) -> None:
        observation = None
        observations = self._stage_observations.get(stage)
        if observations:
            observation = observations.pop()
            if not observations:
                self._stage_observations.pop(stage, None)
        self.trace.add_event(
            "stage",
            stage=stage,
            status=status,
            message=message,
            error=error,
            duration_ms=round((time.monotonic() - started_at) * 1000, 1),
        )
        if observation is not None:
            try:
                observation.finish(
                    status=status,
                    output={"message": message} if message else None,
                    error=error,
                    metadata={
                        "stage": stage,
                        "duration_ms": round((time.monotonic() - started_at) * 1000, 1),
                        "trace_id": self.trace_id,
                    },
                )
            except Exception as exc:  # pragma: no cover - best effort cleanup
                _log.warning("阶段 trace 写入失败", stage=stage, error=str(exc))

    def _record_artifact(self, artifact_type: str, artifact: Any, *, stage: str) -> None:
        payload = (
            artifact.model_dump(exclude_none=True)
            if hasattr(artifact, "model_dump")
            else sanitize_for_json(artifact)
        )
        self.trace.add_event(
            "artifact",
            stage=stage,
            status="created",
            artifact_type=artifact_type,
            artifact=payload if isinstance(payload, dict) else {"value": payload},
        )

    def _build_ping_frame(self, stage: str) -> str:
        payload = {
            "type": "ping",
            "timestamp": datetime.now().isoformat(),
            "trace_id": self.trace_id,
            "stage": stage,
            "message": "单 Agent 正在处理，连接保持中...",
        }
        return encode_sse_event("ping", payload, trace_id=self.trace_id)

    def _build_cancel_complete_frame(self) -> str:
        self._finish_open_stage_observations(status="cancelled", error=self._cancel_reason())
        self._finalize_trace_run(status="cancelled", error=self._cancel_reason())
        payload = {
            "type": "chat_complete",
            "thread_id": self.thread_id,
            "trace_id": self.trace_id,
            "cancelled": True,
            "cancel_reason": self._cancel_reason(),
            "final_content": "",
            "trace": self.trace.model_dump(exclude_none=True),
            "todos": [],
            "timestamp": datetime.now().isoformat(),
        }
        return encode_sse_event("complete", payload, trace_id=self.trace_id)

    async def _drive_step(
        self,
        coro: Any,
        *,
        stage: str,
        interval: float = 10.0,
    ) -> AsyncGenerator[str, None]:
        task = asyncio.create_task(coro)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    self._last_step_result = task.result()
                    return
                yield self._build_ping_frame(stage)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

    async def _invoke_text_model(self, prompt: str, *, operation: str = "model.invoke_text") -> str:
        model = self._resolve_model()
        model_name = str(getattr(model, "model_name", None) or os.getenv("MODEL_NAME") or "unknown")
        with self._trace_run.observation(
            name=f"single_agent.{operation}",
            as_type="generation",
            input={"prompt": prompt, "chars": len(prompt)},
            metadata={
                "operation": operation,
                "model_name": model_name,
                "prompt_chars": len(prompt),
                "thread_id": self.thread_id,
                "trace_id": self.trace_id,
            },
            model=model_name,
        ) as observation:
            response = await model.ainvoke(prompt)
            content = str(getattr(response, "content", "") or "")
            observation.update(
                output={"content": content, "chars": len(content)},
                metadata={
                    "operation": operation,
                    "model_name": model_name,
                    "output_chars": len(content),
                },
            )
            return content

    async def _invoke_json_model(self, prompt: str) -> dict[str, Any]:
        raw_text = await self._invoke_text_model(prompt, operation="model.invoke_json")
        try:
            return loads_json_object(extract_json_text(raw_text))
        except SingleAgentExecutionError as exc:
            repaired_text = await self._invoke_text_model(
                build_json_repair_prompt(raw_text, str(exc)),
                operation="model.invoke_json_repair",
            )
            try:
                return loads_json_object(extract_json_text(repaired_text))
            except SingleAgentExecutionError as repair_exc:
                message = (
                    f"模型 JSON 解析失败：{repair_exc}；"
                    f"原始错误：{exc}；"
                    f"原始响应预览：{preview(raw_text, 500)}"
                )
                raise SingleAgentExecutionError(message) from repair_exc

    def _start_tool_call(self, *, tool_name: str, tool_input: Any, stage: str) -> tuple[str, float, dict[str, Any]]:
        if tool_name not in self.limits.allowed_tools:
            raise SingleAgentExecutionError(f"工具不在单 Agent 白名单内：{tool_name}")
        self._tool_call_count += 1
        if self._tool_call_count > self.limits.max_tool_calls:
            message = f"超过单 Agent 最大工具调用次数限制：{self.limits.max_tool_calls}"
            raise SingleAgentExecutionError(message)
        run_id = f"{tool_name}-{self._tool_call_count}"
        self.trace.add_event(
            "tool_call",
            stage=stage,
            status="started",
            tool=tool_name,
            run_id=run_id,
            input=sanitize_for_json(tool_input),
        )
        payload = {
            "type": "tool_start",
            "tool": tool_name,
            "input": sanitize_for_json(tool_input),
            "stage": stage,
            "current_stage": stage,
            "run_id": run_id,
            "trace_id": self.trace_id,
        }
        return run_id, time.monotonic(), payload

    def _finish_tool_call(
        self,
        *,
        tool_name: str,
        run_id: str,
        started_at: float,
        stage: str,
        output: Any,
    ) -> dict[str, Any]:
        duration_ms = round((time.monotonic() - started_at) * 1000, 1)
        result_preview = preview(output, limit=400)
        self.trace.add_event(
            "tool_result",
            stage=stage,
            status="completed",
            tool=tool_name,
            run_id=run_id,
            result_preview=result_preview,
            duration_ms=duration_ms,
        )
        payload = {
            "type": "tool_end",
            "tool": tool_name,
            "result": sanitize_for_json(output),
            "result_preview": result_preview,
            "truncated": len(stringify(output)) > len(result_preview),
            "stage": stage,
            "current_stage": stage,
            "run_id": run_id,
            "trace_id": self.trace_id,
            "stage_duration_ms": duration_ms,
        }
        serialized = json.dumps(sanitize_for_json(payload), ensure_ascii=False, default=str)
        if len(serialized) > 6000:
            payload.pop("result", None)
            payload["result_preview"] = result_preview[:400] + "...(truncated)"
            payload["truncated"] = True
        return payload

    async def _invoke_restricted_tool(
        self,
        *,
        tool_name: str,
        tool: Any,
        tool_input: Any,
        stage: str,
    ) -> AsyncGenerator[str, None]:
        run_id, started_at, start_payload = self._start_tool_call(
            tool_name=tool_name,
            tool_input=tool_input,
            stage=stage,
        )
        yield encode_sse_event("tool_start", start_payload, trace_id=self.trace_id)
        with self._trace_run.observation(
            name=f"single_agent.tool.{tool_name}",
            as_type="tool",
            input={"tool": tool_name, "stage": stage, "payload": tool_input},
            metadata={
                "tool": tool_name,
                "stage": stage,
                "run_id": run_id,
                "thread_id": self.thread_id,
                "trace_id": self.trace_id,
            },
        ) as observation:
            result = await invoke_tool(tool, tool_input)
            self._last_step_result = result
            observation.update(
                output={"tool": tool_name, "result": result},
                metadata={
                    "tool": tool_name,
                    "stage": stage,
                    "run_id": run_id,
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 1),
                },
            )
        end_payload = self._finish_tool_call(
            tool_name=tool_name,
            run_id=run_id,
            started_at=started_at,
            stage=stage,
            output=result,
        )
        yield encode_sse_event("tool_end", end_payload, trace_id=self.trace_id)

    def _build_error_payload(self, exc: Exception) -> dict[str, Any]:
        model_gateway_error = classify_model_gateway_error(exc)
        if model_gateway_error:
            category, message = model_gateway_error
            return build_server_error_payload(
                message=message,
                error_id=self.request_id,
                trace_id=self.trace_id,
                code=model_error_code(category),
                retryable=False,
                details={"category": category, "runtime": "restricted_single_agent"},
            )
        return build_server_error_payload(
            message="请求处理失败，请稍后重试",
            error_id=self.request_id,
            trace_id=self.trace_id,
            code="INTERNAL_ERROR",
            retryable=False,
            details={"category": "single_agent", "runtime": "restricted_single_agent", "error": str(exc)},
        )
