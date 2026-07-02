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
from ..config import (
    AGENT_TRACE_CONSOLE,
    AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
    AGENT_TRACE_CONSOLE_VERBOSE,
    SINGLE_AGENT_MODEL_INPUT_LIMIT_CHARS,
    SINGLE_AGENT_MODEL_TIMEOUT_SECONDS,
)
from ..observability import NoopTraceRun, TraceRunContext, get_trace_exporter, write_local_trace
from ..context import summarize_resolved_context
from .workflow import summarize_goal_set
from ..diagnosis.adapters import invoke_tool
from ..security.audit import get_security_audit_logger
from ..security.contracts import AuthContext
from ..security.permissions import build_auth_context
from ..security.runtime_context import reset_current_auth_context, set_current_auth_context
from ..security.tool_gateway import authorize_tool_call
from .contracts import AgentTrace, SingleAgentLimits
from .evidence import build_tool_evidence_preview
from .errors import SingleAgentExecutionError
from .flow import SingleAgentFlowMixin
from .support.json_utils import build_json_repair_prompt, extract_json_text, loads_json_object
from .stages import SingleAgentStagesMixin
from .support.serialization import preview, sanitize_for_json, stringify
from .workflow.todos import build_workflow_todos, summarize_workflow_todos, workflow_stage_sequence

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
        auth_context: AuthContext | None = None,
    ) -> None:
        self.message = message
        self.thread_id = thread_id
        self.user_identity = user_identity
        self.auth_context = auth_context or build_auth_context(
            user_id="legacy_admin" if user_identity == "管理员" else "guest",
            display_name=user_identity,
            role="admin" if user_identity == "管理员" else "guest",
        )
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
        self.evidence_bundle: Any | None = None
        self.output_guardrail_result: dict[str, Any] | None = None
        self._last_rendered_answer: Any | None = None
        self._last_structured_analysis: Any | None = None
        self.authorization_decision: Any | None = None
        self._active_allowed_tools: tuple[str, ...] | None = None
        self._workflow_task_decision: Any | None = None
        self._workflow_completed_stages: set[str] = set()
        self._workflow_skipped_stages: set[str] = set()
        self._workflow_current_stage: str | None = None

    def _console_trace(self, message: str, **fields: Any) -> None:
        if not AGENT_TRACE_CONSOLE:
            return
        console_fields = self._compact_console_fields(fields)
        context_fields = {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "auth_role": self.auth_context.role,
            "auth_user_id": self.auth_context.user_id,
            "auth_method": self.auth_context.auth_method,
        }
        if AGENT_TRACE_CONSOLE_VERBOSE:
            context_fields["thread_id"] = preview(self.thread_id, limit=80)
            context_fields["stream_id"] = preview(self.stream_id, limit=80)
        _log.info(
            message,
            **context_fields,
            **console_fields,
        )

    def _console_preview(self, value: Any, *, limit: int | None = None) -> str:
        return " ".join(
            preview(
                sanitize_for_json(value),
                limit=limit or AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
            ).split()
        )

    def _compact_console_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        if AGENT_TRACE_CONSOLE_VERBOSE:
            return fields
        compact: dict[str, Any] = {}
        for key, value in fields.items():
            if key in {"decision", "input_preview", "result_preview"}:
                continue
            if key == "summary":
                compact[key] = self._console_preview(value, limit=140)
            elif key == "error":
                compact[key] = self._console_preview(value, limit=180)
            else:
                compact[key] = value
        return compact

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
        self.evidence_bundle = None
        self.output_guardrail_result = None
        self._last_rendered_answer = None
        self._last_structured_analysis = None
        self.authorization_decision = None
        self._active_allowed_tools = None
        self._workflow_task_decision = None
        self._workflow_completed_stages = set()
        self._workflow_skipped_stages = set()
        self._workflow_current_stage = None

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
            "auth": self.auth_context.audit_summary(),
            "authorization": (
                self.authorization_decision.model_dump()
                if self.authorization_decision is not None
                else {}
            ),
        }
        if metadata:
            trace_metadata.update(metadata)
        if self._workflow_task_decision is not None:
            task_family = getattr(self._workflow_task_decision, "task_family", None)
            if task_family:
                trace_metadata.setdefault("task_family", task_family)
                trace_metadata.setdefault("task_family_reason", getattr(self._workflow_task_decision, "task_family_reason", ""))
                trace_metadata.setdefault("task_family_source", getattr(self._workflow_task_decision, "task_family_source", ""))
                trace_metadata.setdefault(
                    "task_family_warnings",
                    list(getattr(self._workflow_task_decision, "task_family_warnings", []) or []),
                )
            resolved_context = getattr(self._workflow_task_decision, "resolved_context", {}) or {}
            if resolved_context:
                trace_metadata.setdefault("resolved_context", summarize_resolved_context(resolved_context))
            goal_set = getattr(self._workflow_task_decision, "goal_set", {}) or {}
            if goal_set:
                trace_metadata.setdefault("goal_set", summarize_goal_set(goal_set))
            diagnosis_readiness = getattr(self._workflow_task_decision, "diagnosis_readiness", {}) or {}
            if diagnosis_readiness:
                trace_metadata.setdefault("diagnosis_readiness", diagnosis_readiness)
            workorder_action_readiness = getattr(self._workflow_task_decision, "workorder_action_readiness", {}) or {}
            if workorder_action_readiness:
                trace_metadata.setdefault("workorder_action_readiness", workorder_action_readiness)
            manual_confirmation = getattr(self._workflow_task_decision, "manual_confirmation", {}) or {}
            if manual_confirmation:
                trace_metadata.setdefault("manual_confirmation", manual_confirmation)
        if self.evidence_bundle is not None:
            trace_metadata.setdefault("evidence_bundle_id", getattr(self.evidence_bundle, "bundle_id", None))
            trace_metadata.setdefault("evidence_count", len(getattr(self.evidence_bundle, "evidence_items", []) or []))
            trace_metadata.setdefault("claim_count", len(getattr(self.evidence_bundle, "claims", []) or []))
            trace_metadata.setdefault("evidence_quality_checks", getattr(self.evidence_bundle, "quality_checks", {}) or {})
        if self.output_guardrail_result is not None:
            trace_metadata.setdefault("output_guardrail", self.output_guardrail_result)
        self._console_trace(
            "Agent run finished",
            status=status,
            duration_ms=self.trace_duration_ms(),
            tool_call_count=self._tool_call_count,
            error=error,
            summary=self._console_preview(final_answer) if final_answer else "",
        )
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

    def trace_duration_ms(self) -> float | None:
        try:
            started_at = datetime.fromisoformat(self.trace.started_at)
            finished_at = datetime.fromisoformat(self.trace.finished_at) if self.trace.finished_at else datetime.now()
            return round((finished_at - started_at).total_seconds() * 1000, 1)
        except (TypeError, ValueError):
            return None

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
        self._console_trace(
            "Agent stage started",
            stage=stage,
            status="started",
            round=self._round_count,
            summary=message,
        )
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
        self._console_trace(
            "Agent stage finished",
            stage=stage,
            status=status,
            duration_ms=round((time.monotonic() - started_at) * 1000, 1),
            summary=message,
            error=error,
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
        if not AGENT_TRACE_CONSOLE_VERBOSE:
            return
        self._console_trace(
            "Agent artifact recorded",
            stage=stage,
            status="created",
            summary=f"{artifact_type}: {self._console_preview(payload)}",
        )

    def _configure_workflow_tasks(self, decision: Any) -> None:
        self._workflow_task_decision = decision
        self._workflow_completed_stages = {"understand", "access_authorization", "select_workflow_policy"}
        self._workflow_skipped_stages = set()
        self._workflow_current_stage = None

    def _build_workflow_task_payload(
        self,
        *,
        completed_stage: str | None = None,
        skipped_stage: str | None = None,
        current_stage: str | None = None,
        status_hint: str = "",
    ) -> dict[str, Any]:
        if completed_stage:
            self._workflow_completed_stages.add(completed_stage)
            self._workflow_current_stage = None
        if skipped_stage:
            self._workflow_skipped_stages.add(skipped_stage)
            self._workflow_completed_stages.add(skipped_stage)
            self._workflow_current_stage = None
        if current_stage:
            self._workflow_current_stage = current_stage

        todos = build_workflow_todos(
            self._workflow_task_decision,
            completed_stages=self._workflow_completed_stages,
            skipped_stages=self._workflow_skipped_stages,
            current_stage=self._workflow_current_stage,
        )
        summary = summarize_workflow_todos(todos)
        if not status_hint:
            status_hint = "全部完成" if summary.get("pending", 0) + summary.get("in_progress", 0) == 0 else "执行中"
        return {
            "type": "task_update",
            "thread_id": self.thread_id,
            "trace_id": self.trace_id,
            "current_stage": self._workflow_current_stage,
            "todos": todos,
            "summary": summary,
            "status_hint": status_hint,
            "timestamp": datetime.now().isoformat(),
        }

    def _build_workflow_task_update_frame(
        self,
        *,
        completed_stage: str | None = None,
        skipped_stage: str | None = None,
        current_stage: str | None = None,
        status_hint: str = "",
    ) -> str | None:
        if self._workflow_task_decision is None:
            return None
        payload = self._build_workflow_task_payload(
            completed_stage=completed_stage,
            skipped_stage=skipped_stage,
            current_stage=current_stage,
            status_hint=status_hint,
        )
        return encode_sse_event("task_update", payload, trace_id=self.trace_id)

    def _current_workflow_todos_payload(self, *, status_hint: str = "") -> dict[str, Any]:
        return self._build_workflow_task_payload(status_hint=status_hint)

    def _complete_remaining_workflow_tasks(self, *, status_hint: str = "本轮回答已完成") -> str | None:
        if self._workflow_task_decision is None:
            return None
        for stage in workflow_stage_sequence(self._workflow_task_decision):
            self._workflow_completed_stages.add(stage)
        self._workflow_current_stage = None
        payload = self._build_workflow_task_payload(status_hint=status_hint)
        return encode_sse_event("task_update", payload, trace_id=self.trace_id)

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
            "request_id": self.request_id,
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
        if len(prompt) > SINGLE_AGENT_MODEL_INPUT_LIMIT_CHARS:
            prompt = prompt[:SINGLE_AGENT_MODEL_INPUT_LIMIT_CHARS] + "\n\n[TRUNCATED_BY_SINGLE_AGENT_INPUT_LIMIT]"
        model_name = str(getattr(model, "model_name", None) or os.getenv("MODEL_NAME") or "unknown")
        started_at = time.monotonic()
        self._console_trace(
            "Agent model call started",
            operation=operation,
            prompt_chars=len(prompt),
            summary=model_name,
        )
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
            response = await asyncio.wait_for(
                model.ainvoke(prompt),
                timeout=SINGLE_AGENT_MODEL_TIMEOUT_SECONDS,
            )
            content = str(getattr(response, "content", "") or "")
            self._console_trace(
                "Agent model call finished",
                operation=operation,
                duration_ms=round((time.monotonic() - started_at) * 1000, 1),
                prompt_chars=len(prompt),
                output_chars=len(content),
                summary=model_name,
            )
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
        allowed_tools = self._active_allowed_tools if self._active_allowed_tools is not None else self.limits.allowed_tools
        if tool_name not in allowed_tools:
            raise SingleAgentExecutionError(f"工具不在单 Agent 白名单内：{tool_name}")
        tool_authorization = authorize_tool_call(
            self.auth_context,
            tool_name,
            tool_input,
            self._workflow_task_decision,
        )
        if not tool_authorization.allowed:
            get_security_audit_logger().record(
                event_type="tool_denied",
                auth=self.auth_context,
                decision=tool_authorization,
                trace_id=self.trace_id,
                resource={"tool": tool_name, "stage": stage},
            )
            self.trace.add_event(
                "tool_call",
                stage=stage,
                status="denied",
                tool=tool_name,
                input=sanitize_for_json(tool_input),
                error=tool_authorization.reason,
            )
            raise SingleAgentExecutionError(f"工具权限拒绝：{tool_authorization.reason}")
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
        self._console_trace(
            "Agent tool started",
            stage=stage,
            status="started",
            tool=tool_name,
            run_id=run_id,
            tool_call_count=self._tool_call_count,
            input_preview=self._console_preview(tool_input),
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
        self._console_trace(
            "Agent tool finished",
            stage=stage,
            status="completed",
            tool=tool_name,
            run_id=run_id,
            duration_ms=duration_ms,
            result_preview=self._console_preview(output),
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
        evidence_preview = build_tool_evidence_preview(tool_name=tool_name, output=output)
        if evidence_preview:
            payload["evidence"] = sanitize_for_json(evidence_preview)
            payload["evidence_count"] = len(evidence_preview)
            payload["evidence_ids"] = [
                item.get("evidence_id")
                for item in evidence_preview
                if item.get("evidence_id")
            ]
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
            context_token = set_current_auth_context(self.auth_context)
            try:
                result = await invoke_tool(tool, tool_input)
            finally:
                reset_current_auth_context(context_token)
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
