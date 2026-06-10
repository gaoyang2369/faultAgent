"""证据链复核场景 Runner。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncGenerator

from ...quality.evidence import (
    create_grounded_findings,
    list_evidence_records,
    list_finding_links,
    list_findings,
    register_evidence,
    summarize_evidence_coverage,
    summarize_evidence_quality,
)
from ...common.logger import get_logger
from ...common.utils import safe_json_dumps
from ..agents import build_default_plan, create_planning_artifact
from ..artifact_store import get_thread_artifact
from ..contracts import DiagnosisRequest, EvidenceReviewArtifact, PlanningArtifact, WorkflowArtifactEnvelope, WorkflowRunResult, WorkflowType
from ..prompts import build_evidence_review_final_answer_prompt
from ..steps import build_evidence_review_artifact
from .base import BaseScenarioRunner
from .fault_diagnosis import WorkflowExecutionError, _invoke_text_model

_log = get_logger("workflow_evidence_review")
_SUPPORTED_REVIEW_WORKFLOWS = {
    WorkflowType.FAULT_DIAGNOSIS.value,
    WorkflowType.STATUS_INSPECTION.value,
    WorkflowType.MANUAL_QA.value,
}


class EvidenceReviewRunner(BaseScenarioRunner):
    """复核当前线程最近一次业务结论的证据覆盖与质量门禁。"""

    def _build_evidence_review_route_result(self) -> dict[str, Any]:
        return {
            "workflow_type": WorkflowType.EVIDENCE_REVIEW.value,
            "needs_report": False,
            "needs_sql": False,
            "needs_knowledge": False,
            "upstream_artifact_required": True,
            "missing_slots": [],
        }

    def build_request(self) -> DiagnosisRequest:
        """构建证据复核流的最小请求对象。"""

        return DiagnosisRequest(
            user_message=self.message,
            user_identity=self.user_identity,
            needs_report=False,
            report_format="markdown",
            analysis_goal=self.message,
        )

    async def build_planning_artifact(self, request: DiagnosisRequest) -> PlanningArtifact:
        """生成证据复核执行前计划。"""

        started_at = self._iso_now()
        route_result = self.route_result or self._build_evidence_review_route_result()
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
            summary="已生成证据复核 planner 结构化计划",
            started_at=started_at,
            error=planning_artifact.error,
        )
        return planning_artifact

    def load_upstream_artifact(self) -> WorkflowArtifactEnvelope:
        """读取当前线程最近一次可复核的上游 artifact。"""

        started_at = self._iso_now()
        envelope = get_thread_artifact(self.thread_id)
        if envelope is None:
            self._record_step(
                step_name="load_artifact",
                status="error",
                summary="当前线程没有可用于证据复核的上游结构化产物",
                started_at=started_at,
                error="no_artifact",
            )
            raise WorkflowExecutionError("当前线程没有可用于证据复核的已完成分析结果")

        workflow_type = str(envelope.workflow_type)
        if workflow_type not in _SUPPORTED_REVIEW_WORKFLOWS:
            self._record_step(
                step_name="load_artifact",
                status="error",
                summary="当前线程结构化产物类型不支持证据链复核",
                started_at=started_at,
                error=workflow_type,
            )
            raise WorkflowExecutionError(f"当前 workflow_type 不支持证据链复核：{workflow_type}")

        self._record_step(
            step_name="load_artifact",
            status="success",
            summary="已读取当前线程最近一次可复核的结构化产物",
            started_at=started_at,
        )
        return envelope

    def build_evidence_review_artifact(self, envelope: WorkflowArtifactEnvelope) -> EvidenceReviewArtifact:
        """基于上游结果和当前证据上下文生成复核产物。"""

        started_at = self._iso_now()
        artifact = build_evidence_review_artifact(
            envelope,
            create_grounded_findings=create_grounded_findings,
            list_evidence_records=list_evidence_records,
            list_findings=list_findings,
            list_finding_links=list_finding_links,
            register_evidence=register_evidence,
            summarize_evidence_coverage=summarize_evidence_coverage,
            summarize_evidence_quality=summarize_evidence_quality,
        )
        quality_gate_status = str(artifact.quality_gate_status or "unknown")
        step_status = "success" if quality_gate_status == "pass" else "warning"
        self._record_step(
            step_name="evidence_review",
            status=step_status,
            summary="已完成当前线程结论的证据链复核",
            started_at=started_at,
            error=None if step_status == "success" else quality_gate_status,
        )
        return artifact

    async def build_final_answer(self, evidence_review_artifact: EvidenceReviewArtifact) -> str:
        """整理面向用户的复核结果说明。"""

        prompt = build_evidence_review_final_answer_prompt(evidence_review_artifact)
        try:
            final_answer = (await _invoke_text_model(prompt)).strip()
            if final_answer:
                return final_answer
        except Exception as exc:
            _log.warning("证据链复核最终答复整理失败，回退到模板输出", error=str(exc))

        unsupported_lines = (
            "\n".join(f"- {item}" for item in evidence_review_artifact.unsupported_findings[:3])
            or "- 当前未识别到明确的未支撑结论"
        )
        missing_lines = (
            "\n".join(f"- {item}" for item in evidence_review_artifact.missing_evidence_ids[:5])
            or "- 当前未返回明确的缺失证据 ID"
        )
        return (
            f"【证据链复核完成】已对当前线程最近一次 `{evidence_review_artifact.review_target_workflow}` 结果进行复核。\n"
            f"【复核概况】结论 {evidence_review_artifact.total_findings} 条，证据 {evidence_review_artifact.total_evidences} 条，"
            f"覆盖评分 {evidence_review_artifact.coverage_score}，质量门禁状态为 {evidence_review_artifact.quality_gate_status}。\n"
            f"【未充分支撑的结论】\n{unsupported_lines}\n"
            f"【缺失证据线索】\n{missing_lines}\n"
            f"【建议下一步】{evidence_review_artifact.recommended_action or '请继续补充更强的 SQL、知识库或工具证据后再确认结论。'}\n"
            f"【复核摘要】{evidence_review_artifact.review_summary}"
        )

    async def run(self) -> WorkflowRunResult:
        request = self.build_request()
        planning_artifact = await self.build_planning_artifact(request)
        envelope = self.load_upstream_artifact()
        evidence_review_artifact = self.build_evidence_review_artifact(envelope)
        final_answer = await self.build_final_answer(evidence_review_artifact)
        return WorkflowRunResult(
            final_answer=final_answer,
            steps=self.steps,
            request=request,
            evidence_review_artifact=evidence_review_artifact,
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
        """按现有 SSE 契约输出证据链复核流结果。"""

        self.cancel_handle = cancel_handle
        del app
        del request_id
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
                    "message": "证据链复核流已开始执行，正在检查当前线程最近一次结果的证据覆盖。",
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
            evidence_review_artifact = self.build_evidence_review_artifact(envelope)
            final_answer = await self.build_final_answer(evidence_review_artifact)

            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

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
                "todos": current_todos,
                "event_count": event_count,
                "timestamp": datetime.now().isoformat(),
            }
            yield _emit("complete", completion_data)
            _log.info(
                "证据链复核流式请求完成",
                thread_id=self.thread_id,
                stream_id=stream_id,
                event_count=event_count,
                token_count=token_count,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
            )
        except Exception as exc:
            _log.exception("证据链复核流式请求失败", thread_id=self.thread_id, stream_id=stream_id, error=str(exc))
            error_id = request_id or f"workflow-{int(time.time())}"
            yield _emit(
                "server_error",
                self._build_server_error_payload(error_id=error_id, error=exc),
            )
