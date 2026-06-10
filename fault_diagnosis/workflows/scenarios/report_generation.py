"""Standalone workflow runner for report generation."""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, AsyncGenerator

from ...common.logger import get_logger
from ...common.utils import safe_json_dumps
from ..adapters import save_markdown_report_from_analysis
from ..agents import build_default_plan, create_planning_artifact
from ..artifact_store import get_thread_artifact
from ..contracts import DiagnosisRequest, PlanningArtifact, ReportStepArtifact, WorkflowRunResult, WorkflowType
from ..report_mapper import map_artifact_to_report_payload
from .base import BaseScenarioRunner
from .fault_diagnosis import WorkflowExecutionError

_log = get_logger("workflow_report_generation")
_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9._\-]+\.(?:md|html))", re.IGNORECASE)


def _extract_report_url(save_result: str) -> str | None:
    matched = _REPORT_URL_RE.search(save_result or "")
    return matched.group(1) if matched else None


def _extract_report_filename(save_result: str, fallback: str | None = None) -> str | None:
    report_url = _extract_report_url(save_result)
    if report_url:
        return report_url.split("/")[-1]
    return fallback


class ReportGenerationRunner(BaseScenarioRunner):
    """Generate a report from the latest structured artifact in the current thread."""

    def _build_report_route_result(self) -> dict[str, Any]:
        return {
            "workflow_type": WorkflowType.REPORT_GENERATION.value,
            "needs_report": True,
            "needs_sql": False,
            "needs_knowledge": False,
            "upstream_artifact_required": True,
            "missing_slots": [],
        }

    def build_request(self) -> DiagnosisRequest:
        return DiagnosisRequest(
            user_message=self.message,
            user_identity=self.user_identity,
            needs_report=True,
            report_format="markdown",
            analysis_goal=self.message,
        )

    async def build_planning_artifact(self, request: DiagnosisRequest) -> PlanningArtifact:
        """生成报告生成执行前计划。"""

        started_at = self._iso_now()
        route_result = self.route_result or self._build_report_route_result()
        try:
            planning_artifact = await create_planning_artifact(
                request.user_message or self.message,
                request.user_identity or self.user_identity,
                route_result,
            )
        except Exception as exc:  # noqa: BLE001
            planning_artifact = build_default_plan(
                request.user_message or self.message,
                request.user_identity or self.user_identity,
                route_result,
            )
            planning_artifact.fallback_used = True
            planning_artifact.error = f"planner 接入异常，已回退规则计划：{exc}"
        self._record_step(
            step_name="planning",
            status="warning" if planning_artifact.fallback_used else "success",
            summary="已生成报告生成 planner 结构化计划",
            started_at=started_at,
            error=planning_artifact.error,
        )
        return planning_artifact

    def load_upstream_artifact(self):
        started_at = self._iso_now()
        envelope = get_thread_artifact(self.thread_id)
        if envelope is None:
            self._record_step(
                step_name="load_artifact",
                status="error",
                summary="当前线程没有可用于生成报告的结构化结果",
                started_at=started_at,
                error="no_artifact",
            )
            raise WorkflowExecutionError("当前线程没有可用于生成报告的已完成诊断或巡检结果")

        workflow_type = str(envelope.workflow_type)
        if workflow_type not in {WorkflowType.FAULT_DIAGNOSIS.value, WorkflowType.STATUS_INSPECTION.value}:
            self._record_step(
                step_name="load_artifact",
                status="error",
                summary="当前结构化结果类型不支持独立报告生成",
                started_at=started_at,
                error=workflow_type,
            )
            raise WorkflowExecutionError(f"当前 workflow_type 不支持独立生成报告：{workflow_type}")

        self._record_step(
            step_name="load_artifact",
            status="success",
            summary="已读取当前线程最近一次结构化结果",
            started_at=started_at,
        )
        return envelope

    def build_report_artifact(self, envelope) -> ReportStepArtifact:
        started_at = self._iso_now()
        try:
            report_payload = map_artifact_to_report_payload(envelope)
            save_result = save_markdown_report_from_analysis(**report_payload)
            actual_report_filename = _extract_report_filename(save_result, report_payload["report_filename"])
            report_artifact = ReportStepArtifact(
                success=True,
                report_filename=actual_report_filename,
                save_result=save_result,
                error=None,
            )
            self._record_step(
                step_name="report",
                status="success",
                summary="已基于结构化结果生成报告",
                started_at=started_at,
            )
            return report_artifact
        except Exception as exc:
            self._record_step(
                step_name="report",
                status="error",
                summary="基于结构化结果生成报告失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"报告生成失败：{exc}") from exc

    def build_final_answer(self, envelope, report_artifact: ReportStepArtifact) -> str:
        workflow_type = str(envelope.workflow_type)
        source_name = "故障诊断结果" if workflow_type == WorkflowType.FAULT_DIAGNOSIS.value else "状态巡检结果"
        report_url = _extract_report_url(report_artifact.save_result)
        report_file = report_url or report_artifact.report_filename or "未生成"
        return (
            f"已基于当前线程最近一次{source_name}生成报告。\n"
            f"【来源摘要】{envelope.request_summary}\n"
            f"【报告文件】{report_file}\n"
            f"【保存结果】{report_artifact.save_result}"
        )

    async def run(self) -> WorkflowRunResult:
        request = self.build_request()
        planning_artifact = await self.build_planning_artifact(request)
        envelope = self.load_upstream_artifact()
        report_artifact = self.build_report_artifact(envelope)
        final_answer = self.build_final_answer(envelope, report_artifact)
        return WorkflowRunResult(
            final_answer=final_answer,
            steps=self.steps,
            request=request,
            report_artifact=report_artifact,
            planning_artifact=planning_artifact,
            todos=[],
        )

    async def stream_events(
        self,
        app: Any,
        *,
        request_id: str | None = None,
        stream_id: str | None = None,
        cancel_handle: Any = None,
    ) -> AsyncGenerator[str, None]:
        self.cancel_handle = cancel_handle
        del app
        stream_started_at = time.monotonic()
        event_count = 0
        token_count = 0
        current_todos: list = []
        stream_id = (stream_id or "").strip()

        def _emit(event_name: str, payload: dict[str, Any]) -> str:
            return f"event: {event_name}\ndata: {safe_json_dumps(payload)}\n\n"

        try:
            yield _emit(
                "start",
                {
                    "type": "chat_start",
                    "thread_id": self.thread_id,
                    "stream_id": stream_id,
                    "stage": "workflow",
                    "message": "报告生成流程已开始，正在读取当前线程的结构化结果。",
                },
            )
            event_count += 1

            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            request = self.build_request()
            async for ping in self._drive_step(self.build_planning_artifact(request), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            planning_artifact = self._last_step_result

            envelope = self.load_upstream_artifact()
            yield _emit(
                "tool_start",
                {"type": "tool_start", "tool": "save_report", "input": {"workflow_type": str(envelope.workflow_type)}},
            )
            event_count += 1

            report_artifact = self.build_report_artifact(envelope)
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            yield _emit(
                "tool_end",
                {
                    "type": "tool_end",
                    "tool": "save_report",
                    "result_preview": report_artifact.save_result,
                    "truncated": False,
                },
            )
            event_count += 1

            final_answer = self.build_final_answer(envelope, report_artifact)
            if final_answer.strip():
                yield _emit("token", {"type": "token", "content": final_answer})
                token_count += 1
                event_count += 1

            completion_data = {
                "type": "chat_complete",
                "thread_id": self.thread_id,
                "final_content": final_answer,
                "route_result": self._route_payload(),
                "planning": planning_artifact.model_dump(exclude_none=True),
                "governance": (envelope.payload or {}).get("governance", {}),
                "report_filename": report_artifact.report_filename,
                "report_url": _extract_report_url(report_artifact.save_result),
                "report_artifact": {
                    "report_filename": report_artifact.report_filename,
                    "save_result": report_artifact.save_result,
                },
                "todos": current_todos,
                "event_count": event_count,
                "timestamp": datetime.now().isoformat(),
            }
            yield _emit("complete", completion_data)
            _log.info(
                "报告生成流式请求完成",
                thread_id=self.thread_id,
                stream_id=stream_id,
                event_count=event_count,
                token_count=token_count,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
            )
        except Exception as exc:
            _log.exception("报告生成流式请求失败", thread_id=self.thread_id, stream_id=stream_id, error=str(exc))
            error_id = request_id or f"workflow-{int(time.time())}"
            yield _emit(
                "server_error",
                self._build_server_error_payload(error_id=error_id, error=exc),
            )
