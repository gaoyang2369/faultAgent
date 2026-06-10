"""澄清流场景 Runner。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncGenerator

from ...common.logger import get_logger
from ...common.utils import safe_json_dumps
from ..contracts import ClarificationArtifact, DiagnosisRequest, WorkflowRunResult
from ..prompts import build_clarification_final_answer_prompt, build_clarification_understanding_prompt
from .base import BaseScenarioRunner
from .fault_diagnosis import WorkflowExecutionError, _invoke_json_model, _invoke_text_model

_log = get_logger("workflow_clarification")
_VALID_CANDIDATES = {"fault_diagnosis", "status_inspection", "manual_qa", "report_generation"}
_VALID_MISSING_SLOTS = {"equipment_hint", "metric_hint", "fault_code_hint", "time_range_hint"}


class ClarificationRunner(BaseScenarioRunner):
    """处理信息不足、低置信和需要先补充上下文的澄清场景。"""

    async def parse_request(self) -> DiagnosisRequest:
        started_at = self._iso_now()
        prompt = build_clarification_understanding_prompt(self.message, self.user_identity)
        try:
            payload = await _invoke_json_model(prompt)
            request = DiagnosisRequest(
                user_message=self.message,
                user_identity=self.user_identity,
                equipment_hint=payload.get("equipment_hint"),
                metric_hint=payload.get("metric_hint"),
                fault_code_hint=payload.get("fault_code_hint"),
                time_range_hint=payload.get("time_range_hint"),
                needs_report=False,
                report_format="markdown",
                analysis_goal=str(payload.get("analysis_goal") or self.message),
            )
            self._record_step(
                step_name="parse_request",
                status="success",
                summary="已完成澄清流请求理解",
                started_at=started_at,
            )
            return request
        except Exception as exc:
            self._record_step(
                step_name="parse_request",
                status="error",
                summary="澄清流请求理解失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"澄清流请求理解失败：{exc}") from exc

    def _normalize_candidates(self, payload: dict[str, Any]) -> list[str]:
        raw_candidates = payload.get("candidate_workflows") or []
        candidates: list[str] = []
        for item in raw_candidates:
            value = str(item).strip()
            if value in _VALID_CANDIDATES and value not in candidates:
                candidates.append(value)
        if not candidates:
            candidates = ["manual_qa"]
        return candidates

    def _normalize_missing_slots(self, payload: dict[str, Any], request: DiagnosisRequest) -> list[str]:
        raw_slots = payload.get("missing_slots") or []
        missing_slots: list[str] = []
        for item in raw_slots:
            value = str(item).strip()
            if value in _VALID_MISSING_SLOTS and value not in missing_slots:
                missing_slots.append(value)

        inferred_missing_slots: list[str] = []
        if request.equipment_hint is None:
            inferred_missing_slots.append("equipment_hint")

        candidate_set = set(self._normalize_candidates(payload))
        if "fault_diagnosis" in candidate_set and request.fault_code_hint is None:
            inferred_missing_slots.append("fault_code_hint")

        if "status_inspection" in candidate_set and request.time_range_hint is None:
            inferred_missing_slots.append("time_range_hint")

        for item in inferred_missing_slots:
            if item not in missing_slots:
                missing_slots.append(item)

        return missing_slots

    def _normalize_questions(self, payload: dict[str, Any], missing_slots: list[str]) -> list[str]:
        raw_questions = payload.get("clarifying_questions") or []
        questions = [str(item).strip() for item in raw_questions if str(item).strip()]
        if questions:
            return questions[:3]

        default_questions: list[str] = []
        if "equipment_hint" in missing_slots:
            default_questions.append("请先提供设备编号或设备名称。")
        if "fault_code_hint" in missing_slots:
            default_questions.append("请提供故障码、报警码或具体异常现象。")
        if "metric_hint" in missing_slots:
            default_questions.append("请补充你最关注的指标或现象。")
        if "time_range_hint" in missing_slots:
            default_questions.append("请说明你希望查看的时间范围。")
        return default_questions[:3] or ["请补充更具体的设备、异常现象或时间范围信息。"]

    async def run_clarification_step(self, request: DiagnosisRequest) -> ClarificationArtifact:
        started_at = self._iso_now()
        prompt = build_clarification_understanding_prompt(self.message, self.user_identity)
        try:
            payload = await _invoke_json_model(prompt)
            candidates = self._normalize_candidates(payload)
            missing_slots = self._normalize_missing_slots(payload, request)
            questions = self._normalize_questions(payload, missing_slots)
            suggested_next = payload.get("suggested_next_workflow")
            suggested_next_workflow = (
                str(suggested_next).strip() if suggested_next and str(suggested_next).strip() in candidates else None
            )
            artifact = ClarificationArtifact(
                success=True,
                candidate_workflows=candidates,
                missing_slots=missing_slots,
                clarifying_questions=questions,
                reason=str(payload.get("reason") or "当前信息不足，无法安全进入具体业务流。").strip(),
                confidence=str(payload.get("confidence") or "low").strip().lower() or "low",
                suggested_next_workflow=suggested_next_workflow,
                error=None,
            )
            self._record_step(
                step_name="clarification",
                status="success",
                summary="已生成澄清问题与候选场景流",
                started_at=started_at,
            )
            return artifact
        except Exception as exc:
            artifact = ClarificationArtifact(
                success=False,
                candidate_workflows=["manual_qa"],
                missing_slots=["equipment_hint"],
                clarifying_questions=["请先补充设备编号、故障现象或时间范围。"],
                reason="当前信息不足，无法安全进入具体业务流。",
                confidence="low",
                suggested_next_workflow=None,
                error=str(exc),
            )
            self._record_step(
                step_name="clarification",
                status="warning",
                summary="澄清问题生成失败，已回退到保守提示",
                started_at=started_at,
                error=str(exc),
            )
            return artifact

    async def build_final_answer(self, clarification_artifact: ClarificationArtifact) -> str:
        prompt = build_clarification_final_answer_prompt(clarification_artifact)
        try:
            final_answer = (await _invoke_text_model(prompt)).strip()
            if final_answer:
                return final_answer
        except Exception as exc:
            _log.warning("澄清流最终答复整理失败，回退到模板输出", error=str(exc))

        candidate_lines = (
            "\n".join(f"- {item}" for item in clarification_artifact.candidate_workflows)
            or "- 当前无法判断具体场景"
        )
        missing_lines = (
            "\n".join(f"- {item}" for item in clarification_artifact.missing_slots)
            or "- 当前未识别到固定缺失槽位"
        )
        question_lines = (
            "\n".join(f"- {item}" for item in clarification_artifact.clarifying_questions)
            or "- 请补充更具体的问题背景"
        )
        next_flow = clarification_artifact.suggested_next_workflow or "待补充信息后再判断"
        return (
            f"【需要先补充信息】{clarification_artifact.reason}\n"
            f"【候选场景流】\n{candidate_lines}\n"
            f"【当前缺失信息】\n{missing_lines}\n"
            f"【请优先回答】\n{question_lines}\n"
            f"【建议下一步】{next_flow}"
        )

    async def run(self) -> WorkflowRunResult:
        request = await self.parse_request()
        clarification_artifact = await self.run_clarification_step(request)
        final_answer = await self.build_final_answer(clarification_artifact)
        return WorkflowRunResult(
            final_answer=final_answer,
            steps=self.steps,
            request=request,
            clarification_artifact=clarification_artifact,
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
        """按现有 SSE 契约输出澄清流结果。"""

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
                    "message": "澄清流已开始执行，正在识别当前请求缺失的信息。",
                },
            )
            event_count += 1

            async for ping in self._drive_step(self.parse_request(), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            request = self._last_step_result

            async for ping in self._drive_step(self.run_clarification_step(request), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            clarification_artifact = self._last_step_result

            async for ping in self._drive_step(self.build_final_answer(clarification_artifact), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            final_answer = self._last_step_result

            if final_answer.strip():
                yield _emit("token", {"type": "token", "content": final_answer})
                token_count += 1
                event_count += 1

            completion_data = {
                "type": "chat_complete",
                "thread_id": self.thread_id,
                "final_content": final_answer,
                "todos": current_todos,
                "event_count": event_count,
                "timestamp": datetime.now().isoformat(),
            }
            yield _emit("complete", completion_data)
            _log.info(
                "澄清流流式请求完成",
                thread_id=self.thread_id,
                stream_id=stream_id,
                event_count=event_count,
                token_count=token_count,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
            )
        except Exception as exc:
            _log.exception("澄清流流式请求失败", thread_id=self.thread_id, stream_id=stream_id, error=str(exc))
            error_id = request_id or f"workflow-{int(time.time())}"
            yield _emit(
                "server_error",
                self._build_server_error_payload(error_id=error_id, error=exc),
            )
