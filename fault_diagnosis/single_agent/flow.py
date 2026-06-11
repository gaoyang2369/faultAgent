"""Streaming orchestration for the restricted single-agent pipeline."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncGenerator

from ..agent_runtime.sse_adapter import encode_sse_event
from ..common.logger import get_logger
from ..runtime.diagnosis_contract_adapter import build_diagnosis_contract_payload
from .reporting import extract_report_url

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger("single_agent.flow")


class SingleAgentFlowMixin:
    """Top-level SSE state machine for the single-agent runner."""

    async def stream_events(
        self,
        app: "FastAPI",
        *,
        cancel_handle: Any = None,
    ) -> AsyncGenerator[str, None]:
        self.cancel_handle = cancel_handle
        if getattr(app.state, "chat_model", None) is not None and self.model is None:
            self.model = app.state.chat_model

        self._reset_trace_run()
        try:
            self._trace_run = self.trace_exporter.start_run(self._build_trace_context())
        except Exception as exc:  # pragma: no cover - exporter initialization should be best effort
            _log.warning("trace exporter 初始化失败，已降级为本地 no-op", error=str(exc))

        event_count = 0
        token_count = 0
        started_at = time.monotonic()
        self._console_trace(
            "Agent run started",
            status="started",
            summary=self._console_preview(self.message),
        )

        try:
            yield encode_sse_event(
                "start",
                {
                    "type": "chat_start",
                    "thread_id": self.thread_id,
                    "stream_id": self.stream_id,
                    "trace_id": self.trace_id,
                    "stage": "understand",
                    "message": "限制型单 Agent 已开始处理请求。",
                },
                trace_id=self.trace_id,
            )
            event_count += 1

            stage_started = self._start_stage("understand", "理解用户请求并决定必要能力")
            async for ping in self._drive_step(self.understand_request(), stage="understand"):
                yield ping
            request, decision = self._last_step_result
            self._finish_stage("understand", stage_started, message=decision.reason)
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            if decision.report_from_previous_artifact:
                stage_started = self._start_stage("report", "基于当前线程已有结果生成报告")
                async for chunk in self.stream_report_from_previous_artifact():
                    yield chunk
                    event_count += 1
                final_answer, report_artifact = self._last_step_result
                self._finish_stage("report", stage_started, message=report_artifact.save_result)
                stage_started = self._start_stage("final_answer", "整理报告生成结果")
                self._finish_stage("final_answer", stage_started, message="最终回答已生成")
                self.trace.finish(status="completed", final_answer=final_answer)
                self._finish_open_stage_observations(status="completed")
                self._finalize_trace_run(
                    status="completed",
                    final_answer=final_answer,
                    metadata={
                        "event_count": event_count,
                        "token_count": token_count + 1,
                        "decision": decision.model_dump(),
                        "report_filename": report_artifact.report_filename,
                    },
                )
                yield encode_sse_event("token", {"type": "token", "content": final_answer}, trace_id=self.trace_id)
                token_count += 1
                yield encode_sse_event(
                    "complete",
                    {
                        "type": "chat_complete",
                        "thread_id": self.thread_id,
                        "trace_id": self.trace_id,
                        "runtime": "restricted_single_agent",
                        "final_content": final_answer,
                        "report_filename": report_artifact.report_filename,
                        "report_url": extract_report_url(report_artifact.save_result),
                        "decision": decision.model_dump(),
                        "trace": self.trace.model_dump(exclude_none=True),
                        "todos": [],
                        "event_count": event_count,
                        "timestamp": datetime.now().isoformat(),
                    },
                    trace_id=self.trace_id,
                )
                _log.info(
                    "限制型单 Agent 报告续写完成",
                    thread_id=self.thread_id,
                    stream_id=self.stream_id,
                    duration_ms=round((time.monotonic() - started_at) * 1000, 1),
                    event_count=event_count,
                    token_count=token_count,
                )
                return

            if decision.needs_sql:
                stage_started = self._start_stage("sql", "执行受限 SQL 查询")
                async for chunk in self.stream_sql_step(request):
                    yield chunk
                    event_count += 1
                sql_artifact = self._last_step_result
                self._finish_stage("sql", stage_started, message=sql_artifact.summary)
            else:
                stage_started = self._start_stage("sql", "判断后跳过 SQL 查询")
                sql_artifact = self._build_skipped_sql_artifact("本次请求不需要查询设备数据库")
                self._finish_stage("sql", stage_started, status="skipped", message=sql_artifact.summary)
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            if decision.needs_knowledge:
                stage_started = self._start_stage("knowledge", "执行知识库检索")
                async for chunk in self.stream_knowledge_step(request, sql_artifact):
                    yield chunk
                    event_count += 1
                knowledge_artifact = self._last_step_result
                self._finish_stage("knowledge", stage_started, message="知识库检索完成")
            else:
                stage_started = self._start_stage("knowledge", "判断后跳过知识库检索")
                knowledge_artifact = self._build_skipped_knowledge_artifact("本次请求不需要查询知识库")
                self._finish_stage("knowledge", stage_started, status="skipped", message=knowledge_artifact.error or "")
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stage_started = self._start_stage("analysis", "基于可用材料进行诊断分析")
            async for ping in self._drive_step(
                self.analyze(request, sql_artifact, knowledge_artifact, current_time),
                stage="analysis",
            ):
                yield ping
            analysis_artifact = self._last_step_result
            self._finish_stage("analysis", stage_started, message=analysis_artifact.conclusion)
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            if decision.needs_report:
                stage_started = self._start_stage("report", "生成 Markdown 报告")
                async for chunk in self.stream_report_step(
                    request,
                    sql_artifact,
                    knowledge_artifact,
                    analysis_artifact,
                    current_time,
                ):
                    yield chunk
                    event_count += 1
                report_artifact = self._last_step_result
                self._finish_stage("report", stage_started, message=report_artifact.save_result)
            else:
                stage_started = self._start_stage("report", "判断后跳过报告生成")
                report_artifact = self._build_skipped_report_artifact()
                self._finish_stage("report", stage_started, status="skipped", message=report_artifact.save_result)
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            stage_started = self._start_stage("final_answer", "整理最终回答")
            async for ping in self._drive_step(
                self.build_final_answer(analysis_artifact, report_artifact),
                stage="final_answer",
            ):
                yield ping
            final_answer = self._last_step_result
            self._finish_stage("final_answer", stage_started, message="最终回答已生成")
            self.trace.finish(status="completed", final_answer=final_answer)
            saved_envelope = self.save_artifact_envelope(
                request,
                sql_artifact,
                knowledge_artifact,
                analysis_artifact,
                report_artifact,
                final_answer,
                decision,
            )
            diagnosis_contract_payload = build_diagnosis_contract_payload(saved_envelope)

            if final_answer.strip():
                yield encode_sse_event("token", {"type": "token", "content": final_answer}, trace_id=self.trace_id)
                token_count += 1
                event_count += 1

            self._finish_open_stage_observations(status="completed")
            self._finalize_trace_run(
                status="completed",
                final_answer=final_answer,
                metadata={
                    "event_count": event_count,
                    "token_count": token_count,
                    "decision": decision.model_dump(),
                    "report_filename": report_artifact.report_filename,
                },
            )

            complete_payload = {
                "type": "chat_complete",
                "thread_id": self.thread_id,
                "trace_id": self.trace_id,
                "runtime": "restricted_single_agent",
                "final_content": final_answer,
                "report_filename": report_artifact.report_filename,
                "report_url": extract_report_url(report_artifact.save_result),
                "decision": decision.model_dump(),
                "sql_artifact": sql_artifact.model_dump(exclude_none=True),
                "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
                "analysis_artifact": analysis_artifact.model_dump(exclude_none=True),
                "report_artifact": report_artifact.model_dump(exclude_none=True),
                "artifact": saved_envelope.model_dump(exclude_none=True),
                "trace": self.trace.model_dump(exclude_none=True),
                "todos": [],
                "event_count": event_count,
                "timestamp": datetime.now().isoformat(),
            }
            for key, value in diagnosis_contract_payload.items():
                if key not in complete_payload or complete_payload.get(key) in (None, [], {}):
                    complete_payload[key] = value

            yield encode_sse_event(
                "complete",
                complete_payload,
                trace_id=self.trace_id,
            )
            _log.info(
                "限制型单 Agent 流式请求完成",
                thread_id=self.thread_id,
                stream_id=self.stream_id,
                duration_ms=round((time.monotonic() - started_at) * 1000, 1),
                event_count=event_count,
                token_count=token_count,
                tool_call_count=self._tool_call_count,
            )
        except Exception as exc:
            self.trace.finish(status="error", error=str(exc))
            self._finish_open_stage_observations(status="error", error=str(exc))
            self._finalize_trace_run(status="error", error=str(exc))
            _log.exception(
                "限制型单 Agent 流式请求失败",
                thread_id=self.thread_id,
                stream_id=self.stream_id,
                error=str(exc),
            )
            yield encode_sse_event("server_error", self._build_error_payload(exc), trace_id=self.trace_id)
