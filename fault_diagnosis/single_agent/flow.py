"""Streaming orchestration for the restricted single-agent pipeline."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncGenerator

from ..agent_runtime.sse_adapter import encode_sse_event
from ..common.logger import get_logger
from ..diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text
from ..runtime.diagnosis_contract_adapter import build_diagnosis_contract_payload
from .contracts import SingleAgentDecision
from .evidence import build_evidence_bundle, build_output_guardrail_result, initialize_evidence_bundle
from .intent import build_lightweight_conversation_reply
from .reporting import extract_report_url
from .workflow.nodes import (
    build_audit_log_result,
    build_permission_check_result,
    build_resolution_recommendation_result,
    build_risk_check_result,
    workflow_node_enabled,
)

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
        direct_answer = build_lightweight_conversation_reply(self.message)
        initial_stage = "final_answer" if direct_answer else "understand"
        initial_message = (
            "已识别为轻量问候，直接生成回复。"
            if direct_answer
            else "限制型单 Agent 已开始处理请求。"
        )

        try:
            yield encode_sse_event(
                "start",
                {
                    "type": "chat_start",
                    "thread_id": self.thread_id,
                    "stream_id": self.stream_id,
                    "trace_id": self.trace_id,
                    "stage": initial_stage,
                    "message": initial_message,
                },
                trace_id=self.trace_id,
            )
            event_count += 1

            if direct_answer:
                decision = SingleAgentDecision(reason="轻量问候直接回答")
                self.trace.add_event(
                    "decision",
                    stage="final_answer",
                    status="completed",
                    decision=decision.model_dump(),
                    message=decision.reason,
                )
                stage_started = self._start_stage("final_answer", "轻量问候直接回答")
                self._finish_stage("final_answer", stage_started, message="轻量问候已直接回答")
                self.trace.finish(status="completed", final_answer=direct_answer)

                yield encode_sse_event("token", {"type": "token", "content": direct_answer}, trace_id=self.trace_id)
                token_count += 1
                event_count += 1

                self._finish_open_stage_observations(status="completed")
                self._finalize_trace_run(
                    status="completed",
                    final_answer=direct_answer,
                    metadata={
                        "event_count": event_count,
                        "token_count": token_count,
                        "decision": decision.model_dump(),
                        "direct_answer": True,
                    },
                )
                yield encode_sse_event(
                    "complete",
                    {
                        "type": "chat_complete",
                        "thread_id": self.thread_id,
                        "trace_id": self.trace_id,
                        "request_id": self.request_id,
                        "runtime": "restricted_single_agent",
                        "final_content": direct_answer,
                        "report_filename": None,
                        "report_url": None,
                        "decision": decision.model_dump(),
                        "trace": self.trace.model_dump(exclude_none=True),
                        "todos": [],
                        "event_count": event_count,
                        "timestamp": datetime.now().isoformat(),
                    },
                    trace_id=self.trace_id,
                )
                _log.info(
                    "限制型单 Agent 轻量问候直接回复完成",
                    thread_id=self.thread_id,
                    stream_id=self.stream_id,
                    duration_ms=round((time.monotonic() - started_at) * 1000, 1),
                    event_count=event_count,
                    token_count=token_count,
                    tool_call_count=self._tool_call_count,
                )
                return

            stage_started = self._start_stage("understand", "理解用户请求并决定必要能力")
            async for ping in self._drive_step(self.understand_request(), stage="understand"):
                yield ping
            request, decision = self._last_step_result
            self._finish_stage("understand", stage_started, message=decision.reason)
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            stage_started = self._start_stage("select_workflow_policy", "选择任务 workflow policy 和工具白名单")
            self._active_allowed_tools = tuple(decision.runtime_tools)
            self._record_artifact(
                "workflow_route",
                {
                    "primary_task_type": decision.primary_task_type,
                    "route_confidence": decision.route_confidence,
                    "user_goal": decision.user_goal,
                    "objects": decision.objects,
                    "time_window": decision.time_window,
                    "subgoals": decision.subgoals,
                    "missing_slots": decision.missing_slots,
                    "risk_level": decision.risk_level,
                    "requested_output": decision.requested_output,
                    "flags": decision.flags,
                },
                stage="select_workflow_policy",
            )
            self._record_artifact("workflow_policy", decision.workflow_policy, stage="select_workflow_policy")
            self._finish_stage(
                "select_workflow_policy",
                stage_started,
                message=(
                    f"{decision.workflow_policy.get('policy_id', decision.primary_task_type)}："
                    f"启用节点 {', '.join(name for name, enabled in decision.enabled_nodes.items() if enabled) or '无工具节点'}"
                ),
            )
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            stage_started = self._start_stage("initialize_evidence_bundle", "初始化本次任务证据账本")
            self.evidence_bundle = initialize_evidence_bundle(
                trace_id=self.trace_id,
                request=request,
                decision=decision,
            )
            self._record_artifact("evidence_bundle", self.evidence_bundle, stage="initialize_evidence_bundle")
            self._finish_stage(
                "initialize_evidence_bundle",
                stage_started,
                message=f"证据账本已初始化：{self.evidence_bundle.bundle_id}",
            )
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            permission_check_result: dict[str, Any] = {}
            if workflow_node_enabled(decision, "permission_check"):
                stage_started = self._start_stage("permission_check", "检查动作请求权限边界")
                permission_check_result = build_permission_check_result(decision, user_identity=self.user_identity)
                self._record_artifact("permission_check", permission_check_result, stage="permission_check")
                self._finish_stage(
                    "permission_check",
                    stage_started,
                    status="warning",
                    message=permission_check_result.get("reason", ""),
                )
                if self._is_cancelled():
                    yield self._build_cancel_complete_frame()
                    return

            risk_check_result: dict[str, Any] = {}
            if workflow_node_enabled(decision, "risk_check"):
                stage_started = self._start_stage("risk_check", "检查动作请求风险等级")
                risk_check_result = build_risk_check_result(decision)
                self._record_artifact("risk_check", risk_check_result, stage="risk_check")
                self._finish_stage(
                    "risk_check",
                    stage_started,
                    status="warning",
                    message=risk_check_result.get("reason", ""),
                )
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
                        "request_id": self.request_id,
                        "runtime": "restricted_single_agent",
                        "final_content": final_answer,
                        "report_filename": report_artifact.report_filename,
                        "report_url": extract_report_url(report_artifact.save_result),
                        "decision": decision.model_dump(),
                        "workflow_route": {
                            "primary_task_type": decision.primary_task_type,
                            "subgoals": decision.subgoals,
                            "missing_slots": decision.missing_slots,
                        },
                        "workflow_policy": decision.workflow_policy,
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

            if workflow_node_enabled(decision, "sql"):
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

            sql_fault_codes = extract_fault_codes_from_text(
                getattr(sql_artifact, "raw_output", "") or getattr(sql_artifact, "result_preview", "")
            )
            policy_knowledge_setting = (decision.workflow_policy.get("enabled_nodes") or {}).get("knowledge")
            needs_knowledge = workflow_node_enabled(decision, "knowledge") or (
                bool(sql_fault_codes) and policy_knowledge_setting is not False
            )
            if (
                needs_knowledge
                and self._active_allowed_tools is not None
                and "query_knowledge_base" not in self._active_allowed_tools
            ):
                self._active_allowed_tools = (*self._active_allowed_tools, "query_knowledge_base")
            if needs_knowledge:
                knowledge_message = (
                    f"执行知识库检索（故障码：{', '.join(sql_fault_codes)}）"
                    if sql_fault_codes
                    else "执行知识库检索"
                )
                stage_started = self._start_stage("knowledge", knowledge_message)
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

            resolution_recommendation: dict[str, Any] = {}
            if workflow_node_enabled(decision, "resolution_recommendation"):
                stage_started = self._start_stage("resolution_recommendation", "生成处置建议节点产物")
                resolution_recommendation = build_resolution_recommendation_result(
                    decision=decision,
                    analysis_artifact=analysis_artifact,
                )
                self._record_artifact(
                    "resolution_recommendation",
                    resolution_recommendation,
                    stage="resolution_recommendation",
                )
                self._finish_stage(
                    "resolution_recommendation",
                    stage_started,
                    message=f"已整理 {len(resolution_recommendation.get('recommendations', []))} 条处置建议",
                )
                if self._is_cancelled():
                    yield self._build_cancel_complete_frame()
                    return

            if workflow_node_enabled(decision, "workorder_decision"):
                stage_started = self._start_stage("workorder_decision", "判断是否建议生成维修工单")
                async for ping in self._drive_step(
                    self.decide_workorder(request, sql_artifact, knowledge_artifact, analysis_artifact),
                    stage="workorder_decision",
                ):
                    yield ping
                workorder_suggestion = self._last_step_result
                self._finish_stage(
                    "workorder_decision",
                    stage_started,
                    message=workorder_suggestion.reason,
                )
            else:
                stage_started = self._start_stage("workorder_decision", "当前 workflow 未启用工单决策")
                workorder_suggestion = self._build_skipped_workorder_suggestion(
                    "当前任务分类或缺失槽位未触发 workorder_decision 节点"
                )
                self._finish_stage(
                    "workorder_decision",
                    stage_started,
                    status="skipped",
                    message=workorder_suggestion.reason,
                )
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            if workflow_node_enabled(decision, "report"):
                stage_started = self._start_stage("report", "生成可视化 HTML 报告")
                async for chunk in self.stream_report_step(
                    request,
                    sql_artifact,
                    knowledge_artifact,
                    analysis_artifact,
                    workorder_suggestion,
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

            stage_started = self._start_stage("evidence_validation", "生成并校验证据链")
            self.evidence_bundle = build_evidence_bundle(
                trace_id=self.trace_id,
                request=request,
                decision=decision,
                sql_artifact=sql_artifact,
                knowledge_artifact=knowledge_artifact,
                analysis_artifact=analysis_artifact,
                workorder_suggestion=workorder_suggestion,
                report_artifact=report_artifact,
            )
            self._record_artifact("evidence_bundle", self.evidence_bundle, stage="evidence_validation")
            self._finish_stage(
                "evidence_validation",
                stage_started,
                message=(
                    f"证据链校验完成：{len(self.evidence_bundle.evidence_items)} 条证据，"
                    f"{len(self.evidence_bundle.claims)} 条判断"
                ),
            )
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return

            stage_started = self._start_stage("final_answer", "整理最终回答")
            async for ping in self._drive_step(
                self.build_final_answer(analysis_artifact, report_artifact, decision),
                stage="final_answer",
            ):
                yield ping
            final_answer = self._last_step_result
            self._finish_stage("final_answer", stage_started, message="最终回答已生成")

            stage_started = self._start_stage("output_guardrail", "检查最终回答和证据链一致性")
            self.output_guardrail_result = build_output_guardrail_result(final_answer, self.evidence_bundle, decision)
            self._record_artifact("output_guardrail", self.output_guardrail_result, stage="output_guardrail")
            self._finish_stage(
                "output_guardrail",
                stage_started,
                status="completed" if self.output_guardrail_result.get("passed") else "warning",
                message="输出校验通过" if self.output_guardrail_result.get("passed") else "输出校验存在提示",
            )

            audit_log_result: dict[str, Any] = {}
            if workflow_node_enabled(decision, "audit_log"):
                stage_started = self._start_stage("audit_log", "记录动作请求审计信息")
                audit_log_result = build_audit_log_result(
                    decision=decision,
                    permission_check=permission_check_result,
                    risk_check=risk_check_result,
                    output_guardrail=self.output_guardrail_result or {},
                )
                self._record_artifact("audit_log", audit_log_result, stage="audit_log")
                self._finish_stage(
                    "audit_log",
                    stage_started,
                    message="动作请求审计信息已记录",
                )

            stage_started = self._start_stage("save_artifact", "保存诊断产物与证据链")
            saved_envelope = self.save_artifact_envelope(
                request,
                sql_artifact,
                knowledge_artifact,
                analysis_artifact,
                workorder_suggestion,
                report_artifact,
                final_answer,
                decision,
                evidence_bundle=self.evidence_bundle,
                output_guardrail=self.output_guardrail_result,
                workflow_artifacts={
                    "permission_check": permission_check_result,
                    "risk_check": risk_check_result,
                    "resolution_recommendation": resolution_recommendation,
                    "audit_log": audit_log_result,
                },
            )
            diagnosis_contract_payload = build_diagnosis_contract_payload(saved_envelope)
            self._finish_stage("save_artifact", stage_started, message="诊断产物与证据链已保存")
            self.trace.finish(status="completed", final_answer=final_answer)

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
                    "evidence_bundle_id": self.evidence_bundle.bundle_id if self.evidence_bundle else None,
                    "workflow_policy_id": decision.workflow_policy.get("policy_id"),
                    "primary_task_type": decision.primary_task_type,
                },
            )

            complete_payload = {
                "type": "chat_complete",
                "thread_id": self.thread_id,
                "trace_id": self.trace_id,
                "request_id": self.request_id,
                "runtime": "restricted_single_agent",
                "final_content": final_answer,
                "report_filename": report_artifact.report_filename,
                "report_url": extract_report_url(report_artifact.save_result),
                "decision": decision.model_dump(),
                "sql_artifact": sql_artifact.model_dump(exclude_none=True),
                "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
                "analysis_artifact": analysis_artifact.model_dump(exclude_none=True),
                "permission_check": permission_check_result,
                "risk_check": risk_check_result,
                "resolution_recommendation": resolution_recommendation,
                "audit_log": audit_log_result,
                "workorder_decision": workorder_suggestion.model_dump(exclude_none=True),
                "report_artifact": report_artifact.model_dump(exclude_none=True),
                "evidence_bundle": self.evidence_bundle.model_dump(exclude_none=True) if self.evidence_bundle else None,
                "output_guardrail": self.output_guardrail_result or {},
                "workflow_route": {
                    "primary_task_type": decision.primary_task_type,
                    "route_confidence": decision.route_confidence,
                    "objects": decision.objects,
                    "time_window": decision.time_window,
                    "subgoals": decision.subgoals,
                    "missing_slots": decision.missing_slots,
                    "risk_level": decision.risk_level,
                    "requested_output": decision.requested_output,
                },
                "workflow_policy": decision.workflow_policy,
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
