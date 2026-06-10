"""手册问答场景 Runner。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncGenerator

from ...quality.governance import build_workflow_governance_snapshot
from ...common.logger import get_logger
from ...common.utils import safe_json_dumps
from ..adapters import query_knowledge_text
from ..agents import build_default_plan, create_planning_artifact
from ..artifact_store import save_thread_artifact
from ..contracts import (
    DiagnosisRequest,
    EvidenceItem,
    KnowledgeStepArtifact,
    ManualQaArtifact,
    PlanningArtifact,
    WorkflowArtifactEnvelope,
    WorkflowRunResult,
    WorkflowType,
)
from ..prompts import (
    build_manual_qa_answer_prompt,
    build_manual_qa_final_answer_prompt,
    build_manual_qa_understanding_prompt,
)
from ..steps import build_default_knowledge_query, build_knowledge_artifact, parse_request_from_prompt
from .base import BaseScenarioRunner
from .fault_diagnosis import WorkflowExecutionError, _invoke_json_model, _invoke_text_model, _preview

_log = get_logger("workflow_manual_qa")


class ManualQaRunner(BaseScenarioRunner):
    """面向手册释义、操作说明和安全注意事项的场景 Runner。"""

    def _build_manual_qa_route_result(self) -> dict[str, Any]:
        return {
            "workflow_type": WorkflowType.MANUAL_QA.value,
            "needs_report": False,
            "needs_sql": False,
            "needs_knowledge": True,
            "missing_slots": [],
        }

    def build_evidence_items(
        self,
        knowledge_artifact: KnowledgeStepArtifact,
        manual_qa_artifact: ManualQaArtifact,
    ) -> list[EvidenceItem]:
        """构建手册问答流的结构化证据。"""

        knowledge_content = knowledge_artifact.raw_output or "无可靠知识检索结果"
        if knowledge_artifact.snippets:
            knowledge_content = "\n".join(knowledge_artifact.snippets)
        return [
            EvidenceItem(
                source_type="knowledge_base",
                title="手册知识检索摘要",
                content=knowledge_content,
                importance="high" if knowledge_artifact.success else "low",
            ),
            EvidenceItem(
                source_type="manual_qa",
                title="手册问答结论",
                content=manual_qa_artifact.answer,
                importance="medium" if manual_qa_artifact.success else "low",
            ),
        ]

    def save_artifact_envelope(
        self,
        request: DiagnosisRequest,
        knowledge_artifact: KnowledgeStepArtifact,
        manual_qa_artifact: ManualQaArtifact,
        final_answer: str,
        planning_artifact: PlanningArtifact | None = None,
    ) -> WorkflowArtifactEnvelope:
        """保存手册问答流结构化产物，供证据复核和 complete 契约复用。"""

        governance = build_workflow_governance_snapshot(
            route_result=self._route_payload(),
            finding_text=manual_qa_artifact.answer,
            confidence=manual_qa_artifact.confidence,
            has_sql=False,
            has_knowledge=knowledge_artifact.success,
            knowledge_required=True,
        )
        envelope = WorkflowArtifactEnvelope(
            workflow_type=WorkflowType.MANUAL_QA,
            thread_id=self.thread_id,
            created_at=self._iso_now(),
            request_summary=request.analysis_goal or request.user_message,
            final_answer=final_answer,
            report_filename=None,
            payload={
                "request": request.model_dump(exclude_none=True),
                "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
                "manual_qa_artifact": manual_qa_artifact.model_dump(exclude_none=True),
                "planning": planning_artifact.model_dump(exclude_none=True) if planning_artifact else None,
                "route_result": self._route_payload(),
                "governance": governance,
            },
            evidence=self.build_evidence_items(knowledge_artifact, manual_qa_artifact),
        )
        return save_thread_artifact(envelope)

    async def build_planning_artifact(self, request: DiagnosisRequest) -> PlanningArtifact:
        """生成手册问答执行前计划。"""

        started_at = self._iso_now()
        route_result = self.route_result or self._build_manual_qa_route_result()
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
            summary="已生成手册问答 planner 结构化计划",
            started_at=started_at,
            error=planning_artifact.error,
        )
        return planning_artifact

    async def parse_request(self) -> DiagnosisRequest:
        started_at = self._iso_now()
        prompt = build_manual_qa_understanding_prompt(self.message, self.user_identity)
        try:
            request = await parse_request_from_prompt(
                self.message,
                self.user_identity,
                prompt,
                _invoke_json_model,
                needs_report=False,
            )
            self._record_step(
                step_name="parse_request",
                status="success",
                summary="已完成手册问答请求理解",
                started_at=started_at,
            )
            return request
        except Exception as exc:
            self._record_step(
                step_name="parse_request",
                status="error",
                summary="手册问答请求理解失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"手册问答请求理解失败：{exc}") from exc

    def build_knowledge_query(self, request: DiagnosisRequest) -> str:
        """构建手册问答知识检索语句。"""

        return build_default_knowledge_query(request)

    async def run_knowledge_step(self, request: DiagnosisRequest) -> KnowledgeStepArtifact:
        started_at = self._iso_now()
        query = self.build_knowledge_query(request)
        try:
            raw_output = query_knowledge_text(query)
            artifact = build_knowledge_artifact(
                query,
                raw_output,
                fallback_error_message="知识检索未命中",
            )
            self._record_step(
                step_name="knowledge",
                status="success" if artifact.success else "warning",
                summary="手册知识检索完成" if artifact.success else "手册知识不足，准备保守回答",
                started_at=started_at,
                error=None if artifact.success else artifact.error,
            )
            return artifact
        except Exception as exc:
            artifact = KnowledgeStepArtifact(
                success=False,
                query=query,
                snippets=[],
                raw_output="",
                error=str(exc),
            )
            self._record_step(
                step_name="knowledge",
                status="warning",
                summary="手册知识检索失败，将输出保守回答",
                started_at=started_at,
                error=str(exc),
            )
            return artifact

    def _build_conservative_manual_qa_artifact(
        self,
        request: DiagnosisRequest,
        knowledge_artifact: KnowledgeStepArtifact,
    ) -> ManualQaArtifact:
        """知识不足时构建保守回答。"""

        answer = "当前无法从本地知识库获得可靠答案，请补充更具体的设备、故障码或操作场景后再试。"
        if request.fault_code_hint:
            answer = (
                f"当前无法从本地知识库获得关于故障码 {request.fault_code_hint} 的可靠完整说明，"
                "请补充设备型号、操作场景或检查本地手册原文后再试。"
            )
        return ManualQaArtifact(
            success=False,
            question_type="手册问答",
            knowledge_query=knowledge_artifact.query,
            snippets=knowledge_artifact.snippets,
            answer=answer,
            missing_information=["缺少足够的本地知识依据"],
            confidence="low",
            error=knowledge_artifact.error,
        )

    async def run_manual_qa_step(
        self,
        request: DiagnosisRequest,
        knowledge_artifact: KnowledgeStepArtifact,
    ) -> ManualQaArtifact:
        started_at = self._iso_now()
        if not knowledge_artifact.success or not (knowledge_artifact.raw_output or "").strip():
            artifact = self._build_conservative_manual_qa_artifact(request, knowledge_artifact)
            self._record_step(
                step_name="manual_qa",
                status="warning",
                summary="知识不足，已输出保守回答",
                started_at=started_at,
                error=artifact.error,
            )
            return artifact

        try:
            payload = await _invoke_json_model(build_manual_qa_answer_prompt(request, knowledge_artifact))
            artifact = ManualQaArtifact(
                success=True,
                question_type=str(payload.get("question_type") or "手册问答").strip(),
                knowledge_query=knowledge_artifact.query,
                snippets=knowledge_artifact.snippets,
                answer=str(payload.get("answer") or "").strip(),
                missing_information=[
                    str(item).strip() for item in (payload.get("missing_information") or []) if str(item).strip()
                ],
                confidence=str(payload.get("confidence") or "low").strip().lower() or "low",
                error=None,
            )
            if not artifact.answer:
                raise WorkflowExecutionError("手册问答阶段未生成回答")
            self._record_step(
                step_name="manual_qa",
                status="success",
                summary="手册问答整理完成",
                started_at=started_at,
            )
            return artifact
        except Exception as exc:
            artifact = self._build_conservative_manual_qa_artifact(request, knowledge_artifact)
            self._record_step(
                step_name="manual_qa",
                status="warning",
                summary="手册问答整理失败，已回退到保守回答",
                started_at=started_at,
                error=str(exc),
            )
            return artifact

    async def build_final_answer(self, manual_qa_artifact: ManualQaArtifact) -> str:
        prompt = build_manual_qa_final_answer_prompt(manual_qa_artifact)
        try:
            final_answer = (await _invoke_text_model(prompt)).strip()
            if final_answer:
                return final_answer
        except Exception as exc:
            _log.warning("手册问答最终答复整理失败，回退到模板输出", error=str(exc))

        snippets = "\n".join(f"- {item}" for item in manual_qa_artifact.snippets) or "- 当前无可引用知识片段"
        missing_information = (
            "\n".join(f"- {item}" for item in manual_qa_artifact.missing_information)
            or "- 当前无额外缺失信息说明"
        )
        return (
            f"【问题类型】{manual_qa_artifact.question_type}\n"
            f"【回答】{manual_qa_artifact.answer}\n"
            f"【知识依据】\n{snippets}\n"
            f"【仍缺少的信息】\n{missing_information}"
        )

    async def run(self) -> WorkflowRunResult:
        request = await self.parse_request()
        planning_artifact = await self.build_planning_artifact(request)
        knowledge_artifact = await self.run_knowledge_step(request)
        manual_qa_artifact = await self.run_manual_qa_step(request, knowledge_artifact)
        final_answer = await self.build_final_answer(manual_qa_artifact)
        self.save_artifact_envelope(
            request,
            knowledge_artifact,
            manual_qa_artifact,
            final_answer,
            planning_artifact,
        )
        return WorkflowRunResult(
            final_answer=final_answer,
            steps=self.steps,
            request=request,
            knowledge_artifact=knowledge_artifact,
            manual_qa_artifact=manual_qa_artifact,
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
        """按现有 SSE 契约输出手册问答流结果。"""

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
                    "message": "手册问答流已开始执行，正在检索本地知识依据。",
                },
            )
            event_count += 1

            async for ping in self._drive_step(self.parse_request(), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            request = self._last_step_result
            async for ping in self._drive_step(self.build_planning_artifact(request), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            planning_artifact = self._last_step_result

            knowledge_query = self.build_knowledge_query(request)
            yield _emit(
                "tool_start",
                {"type": "tool_start", "tool": "query_knowledge_base", "input": {"query": knowledge_query}},
            )
            event_count += 1
            async for ping in self._drive_step(self.run_knowledge_step(request), stage="tool_call"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            knowledge_artifact = self._last_step_result
            yield _emit(
                "tool_end",
                {
                    "type": "tool_end",
                    "tool": "query_knowledge_base",
                    "result_preview": _preview(knowledge_artifact.raw_output or knowledge_artifact.error or "无结果", limit=400),
                    "truncated": len(knowledge_artifact.raw_output or knowledge_artifact.error or "") > 400,
                },
            )
            event_count += 1

            async for ping in self._drive_step(self.run_manual_qa_step(request, knowledge_artifact), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            manual_qa_artifact = self._last_step_result

            async for ping in self._drive_step(self.build_final_answer(manual_qa_artifact), stage="reasoning"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            final_answer = self._last_step_result
            saved_envelope = self.save_artifact_envelope(
                request,
                knowledge_artifact,
                manual_qa_artifact,
                final_answer,
                planning_artifact,
            )

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
                "governance": (saved_envelope.payload or {}).get("governance", {}),
                "todos": current_todos,
                "event_count": event_count,
                "timestamp": datetime.now().isoformat(),
            }
            yield _emit("complete", completion_data)
            _log.info(
                "手册问答流式请求完成",
                thread_id=self.thread_id,
                stream_id=stream_id,
                event_count=event_count,
                token_count=token_count,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
            )
        except Exception as exc:
            _log.exception("手册问答流式请求失败", thread_id=self.thread_id, stream_id=stream_id, error=str(exc))
            error_id = request_id or f"workflow-{int(time.time())}"
            yield _emit(
                "server_error",
                self._build_server_error_payload(error_id=error_id, error=exc),
            )
