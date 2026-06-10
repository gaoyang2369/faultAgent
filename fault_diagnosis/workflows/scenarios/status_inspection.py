"""状态巡检场景 Runner。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncGenerator

from ...quality.governance import build_workflow_evidence_bundle
from ...common.logger import get_logger
from ...common.utils import safe_json_dumps
from ..adapters import (
    build_sql_tools_map,
    find_sql_tool,
    get_current_time_text,
    invoke_tool,
    query_knowledge_text,
    save_markdown_report_from_analysis,
)
from ..agents import build_default_plan, create_planning_artifact
from ..artifact_store import save_thread_artifact
from ..contracts import (
    DiagnosisRequest,
    EvidenceItem,
    InspectionStepArtifact,
    KnowledgeStepArtifact,
    PlanningArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkflowArtifactEnvelope,
    WorkflowRunResult,
    WorkflowType,
)
from ..prompts import (
    build_status_inspection_analysis_prompt,
    build_status_inspection_final_answer_prompt,
    build_status_inspection_sql_prompt,
    build_status_inspection_understanding_prompt,
)
from ..steps import (
    build_default_knowledge_query,
    build_knowledge_artifact,
    build_sql_plan,
    execute_sql_plan,
    parse_request_from_prompt,
)
from .base import BaseScenarioRunner
from .fault_diagnosis import (
    WorkflowExecutionError,
    _invoke_json_model,
    _invoke_text_model,
    _preview,
    _stringify,
)

_log = get_logger("workflow_status_inspection")


class StatusInspectionRunner(BaseScenarioRunner):
    """面向运行状态与巡检摘要的场景 Runner。"""

    def _build_status_route_result(self, request: DiagnosisRequest) -> dict[str, Any]:
        return {
            "workflow_type": WorkflowType.STATUS_INSPECTION.value,
            "needs_report": bool(request.needs_report),
            "needs_sql": True,
            "needs_knowledge": self.should_query_knowledge(request),
            "missing_slots": [],
        }

    async def build_planning_artifact(self, request: DiagnosisRequest) -> PlanningArtifact:
        """生成状态巡检执行前计划。"""

        started_at = self._iso_now()
        route_result = self.route_result or self._build_status_route_result(request)
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
            summary="已生成巡检 planner 结构化计划",
            started_at=started_at,
            error=planning_artifact.error,
        )
        return planning_artifact

    def build_evidence_items(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        inspection_artifact: InspectionStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact | None,
    ) -> list[EvidenceItem]:
        """构建状态巡检流的结构化证据。"""

        evidence: list[EvidenceItem] = [
            EvidenceItem(
                source_type="sql",
                title="巡检 SQL 摘要",
                content=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
                importance="high",
            ),
            EvidenceItem(
                source_type="inspection",
                title="巡检摘要",
                content=inspection_artifact.summary,
                importance="high",
            ),
        ]
        if inspection_artifact.observed_metrics:
            evidence.append(
                EvidenceItem(
                    source_type="inspection",
                    title="观察指标",
                    content="；".join(inspection_artifact.observed_metrics),
                    importance="medium",
                )
            )
        if inspection_artifact.detected_anomalies:
            evidence.append(
                EvidenceItem(
                    source_type="inspection",
                    title="发现异常",
                    content="；".join(inspection_artifact.detected_anomalies),
                    importance="high" if inspection_artifact.risk_level == "high" else "medium",
                )
            )
        if knowledge_artifact is not None:
            evidence.append(
                EvidenceItem(
                    source_type="knowledge_base",
                    title="巡检知识补充",
                    content=knowledge_artifact.raw_output or "无可靠知识补充结果",
                    importance="medium" if knowledge_artifact.success else "low",
                )
            )
        if request.metric_hint:
            evidence.append(
                EvidenceItem(
                    source_type="inspection",
                    title="指标提示",
                    content=request.metric_hint,
                    importance="low",
                )
            )
        return evidence

    def save_artifact_envelope(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        inspection_artifact: InspectionStepArtifact,
        report_artifact: ReportStepArtifact | None,
        final_answer: str,
        knowledge_artifact: KnowledgeStepArtifact | None = None,
        planning_artifact: PlanningArtifact | None = None,
    ) -> WorkflowArtifactEnvelope:
        """保存状态巡检流结构化产物。"""

        evidence_bundle = build_workflow_evidence_bundle(
            route_result=self._route_payload(),
            finding_text=inspection_artifact.summary,
            confidence=inspection_artifact.confidence,
            has_sql=sql_artifact.success,
            sql_title="Workflow SQL 结果",
            sql_summary=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
            sql_query="; ".join(sql_artifact.sql_used or []),
            has_knowledge=knowledge_artifact.success if knowledge_artifact else False,
            knowledge_title="Workflow 知识检索结果",
            knowledge_summary=knowledge_artifact.raw_output if knowledge_artifact else "",
            knowledge_query=knowledge_artifact.query if knowledge_artifact else "",
            knowledge_required=False,
        )
        governance = evidence_bundle["governance"]

        envelope = WorkflowArtifactEnvelope(
            workflow_type=WorkflowType.STATUS_INSPECTION,
            thread_id=self.thread_id,
            created_at=self._iso_now(),
            request_summary=request.analysis_goal or request.user_message,
            final_answer=final_answer,
            report_filename=report_artifact.report_filename if report_artifact else None,
            payload={
                "request": request.model_dump(exclude_none=True),
                "sql_artifact": sql_artifact.model_dump(exclude_none=True),
                "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True) if knowledge_artifact else None,
                "inspection_artifact": inspection_artifact.model_dump(exclude_none=True),
                "report_artifact": report_artifact.model_dump(exclude_none=True) if report_artifact else None,
                "planning": planning_artifact.model_dump(exclude_none=True) if planning_artifact else None,
                "route_result": self._route_payload(),
                "governance": governance,
                "report_gate_summary": evidence_bundle["report_gate_summary"],
                "findings_snapshot": evidence_bundle["findings_snapshot"],
                "finding_links_snapshot": evidence_bundle["finding_links_snapshot"],
                "evidence_records_snapshot": evidence_bundle["evidence_records_snapshot"],
            },
            evidence=self.build_evidence_items(request, sql_artifact, inspection_artifact, knowledge_artifact),
        )
        return save_thread_artifact(envelope)

    async def parse_request(self) -> DiagnosisRequest:
        started_at = self._iso_now()
        prompt = build_status_inspection_understanding_prompt(self.message, self.user_identity)
        try:
            request = await parse_request_from_prompt(
                self.message,
                self.user_identity,
                prompt,
                _invoke_json_model,
                needs_report=None,
            )
            self._record_step(
                step_name="parse_request",
                status="success",
                summary="已完成巡检请求理解",
                started_at=started_at,
            )
            return request
        except Exception as exc:
            self._record_step(
                step_name="parse_request",
                status="error",
                summary="巡检请求理解失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"巡检请求理解失败：{exc}") from exc

    async def run_sql_step(self, request: DiagnosisRequest) -> SqlStepArtifact:
        started_at = self._iso_now()
        try:
            sql_query, summary = await build_sql_plan(
                build_status_inspection_sql_prompt(request),
                _invoke_json_model,
                default_summary="已生成巡检 SQL 查询",
            )
            if not sql_query:
                raise WorkflowExecutionError("巡检 SQL 生成结果为空")

            artifact = await execute_sql_plan(
                sql_query,
                summary,
                build_sql_tools_map=build_sql_tools_map,
                find_sql_tool=find_sql_tool,
                invoke_tool=invoke_tool,
                stringify=_stringify,
                preview=_preview,
            )
            self._record_step(
                step_name="sql",
                status="success",
                summary="巡检 SQL 查询完成",
                started_at=started_at,
            )
            return artifact
        except Exception as exc:
            self._record_step(
                step_name="sql",
                status="error",
                summary="巡检 SQL 查询失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"巡检 SQL 查询失败：{exc}") from exc

    def should_query_knowledge(self, request: DiagnosisRequest) -> bool:
        """只有在需要补充风险或依据时才检索知识库。"""

        message = request.user_message
        return any(keyword in message for keyword in ("风险", "注意事项", "依据", "原因"))

    def build_knowledge_query(self, request: DiagnosisRequest) -> str:
        """构建巡检知识补充查询语句。"""

        return build_default_knowledge_query(request, "状态巡检", "风险")

    async def run_optional_knowledge_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
    ) -> KnowledgeStepArtifact | None:
        del sql_artifact
        if not self.should_query_knowledge(request):
            return None

        started_at = self._iso_now()
        query = self.build_knowledge_query(request)
        try:
            raw_output = query_knowledge_text(query)
            artifact = build_knowledge_artifact(
                query,
                raw_output,
                fallback_error_message="巡检知识补充未命中",
            )
            self._record_step(
                step_name="knowledge",
                status="success" if artifact.success else "warning",
                summary="巡检知识补充完成" if artifact.success else "巡检知识补充未命中或结果不足",
                started_at=started_at,
                error=None if artifact.success else artifact.error,
            )
            return artifact
        except Exception as exc:
            self._record_step(
                step_name="knowledge",
                status="warning",
                summary="巡检知识补充失败，后续将仅依赖 SQL 结果输出",
                started_at=started_at,
                error=str(exc),
            )
            return KnowledgeStepArtifact(
                success=False,
                query=query,
                snippets=[],
                raw_output="",
                error=str(exc),
            )

    async def run_inspection_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact | None,
        current_time: str,
    ) -> InspectionStepArtifact:
        started_at = self._iso_now()
        try:
            payload = await _invoke_json_model(
                build_status_inspection_analysis_prompt(request, sql_artifact, knowledge_artifact, current_time)
            )
            artifact = InspectionStepArtifact(
                success=True,
                summary=str(payload.get("summary") or "").strip(),
                observed_metrics=[
                    str(item).strip() for item in (payload.get("observed_metrics") or []) if str(item).strip()
                ],
                detected_anomalies=[
                    str(item).strip() for item in (payload.get("detected_anomalies") or []) if str(item).strip()
                ],
                risk_level=str(payload.get("risk_level") or "low").strip().lower() or "low",
                suggested_actions=[
                    str(item).strip() for item in (payload.get("suggested_actions") or []) if str(item).strip()
                ],
                confidence=str(payload.get("confidence") or "low").strip().lower() or "low",
                error=None,
            )
            if not artifact.summary:
                raise WorkflowExecutionError("巡检分析阶段未生成摘要")
            self._record_step(
                step_name="inspection",
                status="success",
                summary="状态巡检分析完成",
                started_at=started_at,
            )
            return artifact
        except Exception as exc:
            self._record_step(
                step_name="inspection",
                status="error",
                summary="状态巡检分析失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"状态巡检分析失败：{exc}") from exc

    async def run_report_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        inspection_artifact: InspectionStepArtifact,
        current_time: str,
    ) -> ReportStepArtifact:
        started_at = self._iso_now()
        if not request.needs_report:
            artifact = ReportStepArtifact(
                success=False,
                report_filename=None,
                save_result="本次巡检请求无需生成报告",
                error=None,
            )
            self._record_step(
                step_name="report",
                status="skipped",
                summary="本次巡检未生成报告",
                started_at=started_at,
            )
            return artifact

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"dcma_status_inspection_{timestamp}_{self.thread_id[-6:]}"
        try:
            evidence_bundle = build_workflow_evidence_bundle(
                route_result=self._route_payload(),
                finding_text=inspection_artifact.summary,
                confidence=inspection_artifact.confidence,
                has_sql=sql_artifact.success,
                sql_title="Workflow SQL 结果",
                sql_summary=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
                sql_query="; ".join(sql_artifact.sql_used or []),
                has_knowledge=False,
                knowledge_required=False,
            )
            save_result = save_markdown_report_from_analysis(
                title="DCMA 状态巡检报告",
                report_time=current_time,
                diagnosis_object=request.equipment_hint or "DCMA 系统",
                diagnosis_type="状态巡检",
                executive_summary=inspection_artifact.summary,
                diagnosis_overview="本报告由状态巡检流生成，聚焦运行概览、风险信号和建议动作。",
                diagnosis_details=(
                    f"【巡检 SQL 摘要】\n{sql_artifact.result_preview or sql_artifact.raw_output or '无'}\n\n"
                    f"【观察指标】\n{'; '.join(inspection_artifact.observed_metrics) or '无'}\n\n"
                    f"【发现异常】\n{'; '.join(inspection_artifact.detected_anomalies) or '无'}"
                ),
                fault_inference=inspection_artifact.summary,
                repair_recommendations="\n".join(f"- {item}" for item in inspection_artifact.suggested_actions)
                or "- 暂无具体建议动作",
                preventive_maintenance="建议结合巡检结果持续观察关键指标变化。",
                diagnosis_basis=(
                    f"SQL 摘要：{sql_artifact.summary}\n"
                    f"SQL 语句：{'; '.join(sql_artifact.sql_used) or '无'}\n"
                    f"风险等级：{inspection_artifact.risk_level}\n"
                    f"观察指标：{'; '.join(inspection_artifact.observed_metrics) or '无'}"
                ),
                report_filename=report_filename,
                report_gate_summary=evidence_bundle["report_gate_summary"],
                findings_snapshot=evidence_bundle["findings_snapshot"],
                finding_links_snapshot=evidence_bundle["finding_links_snapshot"],
                evidence_records_snapshot=evidence_bundle["evidence_records_snapshot"],
            )
            artifact = ReportStepArtifact(
                success=True,
                report_filename=report_filename,
                save_result=save_result,
                error=None,
            )
            self._record_step(
                step_name="report",
                status="success",
                summary="状态巡检 Markdown 报告已生成",
                started_at=started_at,
            )
            return artifact
        except Exception as exc:
            artifact = ReportStepArtifact(
                success=False,
                report_filename=report_filename,
                save_result="",
                error=str(exc),
            )
            self._record_step(
                step_name="report",
                status="warning",
                summary="巡检报告生成失败，保留巡检摘要",
                started_at=started_at,
                error=str(exc),
            )
            return artifact

    async def build_final_answer(
        self,
        inspection_artifact: InspectionStepArtifact,
        report_artifact: ReportStepArtifact | None,
    ) -> str:
        prompt = build_status_inspection_final_answer_prompt(inspection_artifact, report_artifact)
        try:
            final_answer = (await _invoke_text_model(prompt)).strip()
            if final_answer:
                return final_answer
        except Exception as exc:
            _log.warning("巡检最终答复整理失败，回退到模板输出", error=str(exc))

        observed_metrics = "\n".join(f"- {item}" for item in inspection_artifact.observed_metrics) or "- 暂无明确指标摘要"
        anomalies = "\n".join(f"- {item}" for item in inspection_artifact.detected_anomalies) or "- 当前未发现明确异常"
        actions = "\n".join(f"- {item}" for item in inspection_artifact.suggested_actions) or "- 建议继续观察"
        report_name = report_artifact.report_filename if report_artifact and report_artifact.report_filename else "未生成"
        return (
            f"【巡检摘要】{inspection_artifact.summary}\n"
            f"【观察指标】\n{observed_metrics}\n"
            f"【异常与风险】\n{anomalies}\n"
            f"【风险等级】{inspection_artifact.risk_level}\n"
            f"【建议动作】\n{actions}\n"
            f"【报告文件】{report_name}"
        )

    async def run(self) -> WorkflowRunResult:
        request = await self.parse_request()
        planning_artifact = await self.build_planning_artifact(request)
        current_time = get_current_time_text()
        sql_artifact = await self.run_sql_step(request)
        knowledge_artifact = await self.run_optional_knowledge_step(request, sql_artifact)
        inspection_artifact = await self.run_inspection_step(request, sql_artifact, knowledge_artifact, current_time)
        report_artifact = await self.run_report_step(request, sql_artifact, inspection_artifact, current_time)
        final_answer = await self.build_final_answer(inspection_artifact, report_artifact)
        self.save_artifact_envelope(
            request,
            sql_artifact,
            inspection_artifact,
            report_artifact,
            final_answer,
            knowledge_artifact,
            planning_artifact,
        )
        return WorkflowRunResult(
            final_answer=final_answer,
            steps=self.steps,
            request=request,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
            inspection_artifact=inspection_artifact,
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
        """按现有 SSE 契约输出状态巡检流结果。"""

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
                    "message": "状态巡检流已开始执行，正在整理巡检目标并准备运行概览。",
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
            current_time = get_current_time_text()

            yield _emit("tool_start", {"type": "tool_start", "tool": "sql_db_query", "input": {"goal": request.analysis_goal}})
            event_count += 1
            async for ping in self._drive_step(self.run_sql_step(request), stage="tool_call"):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            sql_artifact = self._last_step_result
            yield _emit(
                "tool_end",
                {
                    "type": "tool_end",
                    "tool": "sql_db_query",
                    "result_preview": sql_artifact.result_preview or sql_artifact.summary,
                    "truncated": len(sql_artifact.raw_output) > len(sql_artifact.result_preview),
                },
            )
            event_count += 1

            knowledge_artifact = None
            if self.should_query_knowledge(request):
                knowledge_query = self.build_knowledge_query(request)
                yield _emit(
                    "tool_start",
                    {"type": "tool_start", "tool": "query_knowledge_base", "input": {"query": knowledge_query}},
                )
                event_count += 1
                async for ping in self._drive_step(
                    self.run_optional_knowledge_step(request, sql_artifact),
                    stage="tool_call",
                ):
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
                        "result_preview": _preview(knowledge_artifact.raw_output, limit=400),
                        "truncated": len(knowledge_artifact.raw_output) > 400,
                    },
                )
                event_count += 1

            async for ping in self._drive_step(
                self.run_inspection_step(request, sql_artifact, knowledge_artifact, current_time),
                stage="reasoning",
            ):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            inspection_artifact = self._last_step_result

            if request.needs_report:
                yield _emit(
                    "tool_start",
                    {"type": "tool_start", "tool": "save_report", "input": {"report_format": request.report_format}},
                )
                event_count += 1
            async for ping in self._drive_step(
                self.run_report_step(request, sql_artifact, inspection_artifact, current_time),
                stage="tool_call",
            ):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            report_artifact = self._last_step_result
            if request.needs_report:
                yield _emit(
                    "tool_end",
                    {
                        "type": "tool_end",
                        "tool": "save_report",
                        "result_preview": report_artifact.save_result or report_artifact.error or "未生成报告",
                        "truncated": False,
                    },
                )
                event_count += 1

            async for ping in self._drive_step(
                self.build_final_answer(inspection_artifact, report_artifact),
                stage="reasoning",
            ):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            final_answer = self._last_step_result
            saved_envelope = self.save_artifact_envelope(
                request,
                sql_artifact,
                inspection_artifact,
                report_artifact,
                final_answer,
                knowledge_artifact,
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
                "governance": (saved_envelope.payload or {}).get("governance", {}),
                "todos": current_todos,
                "event_count": event_count,
                "timestamp": datetime.now().isoformat(),
            }
            yield _emit("complete", completion_data)
            _log.info(
                "状态巡检流式请求完成",
                thread_id=self.thread_id,
                stream_id=stream_id,
                event_count=event_count,
                token_count=token_count,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
            )
        except Exception as exc:
            _log.exception("状态巡检流式请求失败", thread_id=self.thread_id, stream_id=stream_id, error=str(exc))
            error_id = request_id or f"workflow-{int(time.time())}"
            yield _emit(
                "server_error",
                self._build_server_error_payload(error_id=error_id, error=exc),
            )
