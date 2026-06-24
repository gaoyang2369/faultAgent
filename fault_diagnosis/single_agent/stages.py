"""Business stage handlers for the restricted single-agent pipeline."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, AsyncGenerator

from ..common.logger import get_logger
from ..diagnosis.adapters import build_sql_tools_map, find_sql_tool
from ..diagnosis.analysis import diagnose_dcma_runtime
from ..diagnosis.artifact_store import get_thread_artifact, save_thread_artifact
from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    DiagnosisRequest,
    EvidenceBundle,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ..diagnosis.report_mapper import map_artifact_to_report_payload
from ..diagnosis.steps import (
    build_default_knowledge_query,
    build_knowledge_artifact,
    build_request_from_payload,
    build_sql_plan,
)
from ..diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text
from ..security.sql_acl import apply_sql_acl
from .artifacts import build_diagnosis_artifact_envelope
from .contracts import SingleAgentDecision
from .errors import SingleAgentExecutionError
from .intent import (
    decide_capabilities,
    fallback_understanding_payload,
    looks_like_report_handoff,
    normalize_equipment_hint,
    should_use_rule_based_understanding,
)
from .final_answer import build_templated_final_answer
from .prompts import (
    build_single_agent_analysis_prompt,
    build_single_agent_understanding_prompt,
)
from .reporting import (
    build_report_payload,
    extract_report_filename,
    extract_report_url,
)
from .support.serialization import preview, stringify
from .sql_safety import build_fallback_sql_query, build_sql_prompt, has_unknown_sql_table, is_readonly_sql
from .sql_safety import build_fast_sql_plan
from .support.tool_access import get_knowledge_tool, get_report_tool
from .workorder_suggestions import build_workorder_suggestion

_log = get_logger("single_agent.stages")


class SingleAgentStagesMixin:
    """Stage-level behavior split out from the public runner facade."""

    async def understand_request(self) -> tuple[DiagnosisRequest, SingleAgentDecision]:
        report_from_previous_artifact = (
            looks_like_report_handoff(self.message)
            and get_thread_artifact(self.thread_id) is not None
        )
        if report_from_previous_artifact:
            payload = fallback_understanding_payload(self.message, self.user_identity)
            payload["needs_report"] = True
        elif should_use_rule_based_understanding(self.message):
            payload = fallback_understanding_payload(self.message, self.user_identity)
        else:
            try:
                payload = await self._invoke_json_model(
                    build_single_agent_understanding_prompt(self.message, self.user_identity)
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "请求理解模型失败，使用规则 fallback",
                    thread_id=self.thread_id,
                    error=str(exc),
                )
                payload = fallback_understanding_payload(self.message, self.user_identity)
        payload["equipment_hint"] = normalize_equipment_hint(payload.get("equipment_hint"))

        request = build_request_from_payload(
            self.message,
            self.user_identity,
            payload,
            needs_report=None,
            report_format=str(payload.get("report_format") or "markdown"),
        )
        decision = decide_capabilities(
            payload=payload,
            request=request,
            message=self.message,
            report_from_previous_artifact=report_from_previous_artifact,
        )
        self.trace.add_event(
            "decision",
            stage="understand",
            status="completed",
            decision=decision.model_dump(),
            message=decision.reason,
        )
        self._console_trace(
            "Agent decision made",
            stage="understand",
            status="completed",
            decision=decision.model_dump(),
            summary=decision.reason,
        )
        self._record_artifact("request", request, stage="understand")
        return request, decision

    async def stream_sql_step(self, request: DiagnosisRequest) -> AsyncGenerator[str, None]:
        fast_plan = build_fast_sql_plan(request)
        skip_checker = fast_plan is not None
        if fast_plan is not None:
            sql_query, summary = fast_plan
        else:
            prompt = build_sql_prompt(request)
            sql_query, summary = await build_sql_plan(
                prompt,
                self._invoke_json_model,
                default_summary="已生成 SQL 查询",
            )
            if not sql_query or not is_readonly_sql(sql_query) or has_unknown_sql_table(sql_query):
                sql_query = build_fallback_sql_query(request)
                summary = "已使用受限 fallback 查询最近设备故障与关键指标数据"

        acl_result = apply_sql_acl(
            sql_query,
            auth=self.auth_context,
            request=request,
            decision=self._workflow_task_decision,
        )
        if not acl_result.allowed:
            artifact = SqlStepArtifact(
                success=False,
                summary="SQL 查询被数据权限策略拦截",
                error=acl_result.reason,
                access_scope=dict(getattr(self._workflow_task_decision, "access_scope", {}) or {}),
            )
            self._record_artifact("sql", artifact, stage="sql")
            self._last_step_result = artifact
            return
        sql_query = acl_result.sql_query
        filters_applied = list(acl_result.filters_applied)

        tools_map = build_sql_tools_map()
        checker_tool = find_sql_tool(tools_map, "sql_db_query_checker", False)
        if checker_tool is not None and not skip_checker:
            async for chunk in self._invoke_restricted_tool(
                tool_name="sql_db_query_checker",
                tool=checker_tool,
                tool_input={"query": sql_query},
                stage="sql",
            ):
                yield chunk
            checked_query_text = stringify(self._last_step_result).strip()
            if (
                checked_query_text
                and is_readonly_sql(checked_query_text)
                and not has_unknown_sql_table(checked_query_text)
            ):
                checked_acl_result = apply_sql_acl(
                    checked_query_text,
                    auth=self.auth_context,
                    request=request,
                    decision=self._workflow_task_decision,
                )
                if not checked_acl_result.allowed:
                    artifact = SqlStepArtifact(
                        success=False,
                        summary="SQL checker 返回结果未通过数据权限复检",
                        error=checked_acl_result.reason,
                        access_scope=dict(getattr(self._workflow_task_decision, "access_scope", {}) or {}),
                    )
                    self._record_artifact("sql", artifact, stage="sql")
                    self._last_step_result = artifact
                    return
                sql_query = checked_acl_result.sql_query
                filters_applied = list(dict.fromkeys([*filters_applied, *checked_acl_result.filters_applied]))

        query_tool = find_sql_tool(tools_map, "sql_db_query", True)
        async for chunk in self._invoke_restricted_tool(
            tool_name="sql_db_query",
            tool=query_tool,
            tool_input={"query": sql_query},
            stage="sql",
        ):
            yield chunk
        raw_output = self._last_step_result
        artifact = SqlStepArtifact(
            success=True,
            summary=summary,
            sql_used=[sql_query],
            result_preview=preview(raw_output),
            raw_output=stringify(raw_output),
            access_scope=dict(getattr(self._workflow_task_decision, "access_scope", {}) or {}),
            filters_applied=filters_applied,
        )
        self._record_artifact("sql", artifact, stage="sql")
        self._last_step_result = artifact

    async def stream_knowledge_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact | None,
    ) -> AsyncGenerator[str, None]:
        fault_codes = extract_fault_codes_from_text(
            (sql_artifact.raw_output or sql_artifact.result_preview) if sql_artifact else ""
        )
        fault_code_query = (
            " ".join(f"故障码 {code} 含义 触发原因 处理步骤" for code in fault_codes)
            if fault_codes
            else ""
        )
        query = build_default_knowledge_query(
            request,
            fault_code_query,
            sql_artifact.summary if sql_artifact and sql_artifact.success else "",
        )
        async for chunk in self._invoke_restricted_tool(
            tool_name="query_knowledge_base",
            tool=get_knowledge_tool(),
            tool_input={"query": query},
            stage="knowledge",
        ):
            yield chunk
        raw_output = stringify(self._last_step_result)
        artifact = build_knowledge_artifact(
            query,
            raw_output,
            fallback_error_message="知识检索未命中",
        )
        self._record_artifact("knowledge", artifact, stage="knowledge")
        self._last_step_result = artifact

    async def analyze(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        current_time: str,
    ) -> AnalysisStepArtifact:
        decision = self._workflow_task_decision
        authorization = dict(getattr(decision, "authorization", {}) or {})
        task_type = str(getattr(decision, "primary_task_type", "") or "")
        if self.auth_context.role == "guest" and task_type != "knowledge_qa":
            is_degraded = authorization.get("mode") == "degrade"
            recommendations = list(knowledge_artifact.snippets[:2]) if task_type == "alarm_triage" else []
            if is_degraded:
                recommendations.append("如需故障诊断、健康评估或诊断报告，请使用具备设备权限的工程师账号。")
            artifact = AnalysisStepArtifact(
                success=True,
                conclusion=(
                    "当前身份仅提供最近一小时运行状态与公开处理意见，不形成故障诊断或根因结论。"
                    if is_degraded
                    else "已按当前身份范围整理最近一小时运行状态；该结果仅表示数据现状。"
                ),
                basis=[
                    item
                    for item in [sql_artifact.summary, sql_artifact.result_preview, knowledge_artifact.error]
                    if item
                ][:3],
                probable_causes=[],
                verification_items=[],
                recommendations=recommendations,
                missing_information=(
                    ["故障诊断权限与更完整的授权数据窗口"] if is_degraded else []
                ),
                confidence="low",
            )
            self._record_artifact("analysis", artifact, stage="analysis")
            return artifact
        structured_analysis = diagnose_dcma_runtime(
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
            request=request,
            decision=decision,
        )
        if structured_analysis.assessment.success:
            self._last_structured_analysis = structured_analysis
            self._record_artifact("structured_analysis", structured_analysis.assessment, stage="analysis")
            self._record_artifact("analysis", structured_analysis.analysis_artifact, stage="analysis")
            return structured_analysis.analysis_artifact

        try:
            payload = await self._invoke_json_model(
                build_single_agent_analysis_prompt(
                    request,
                    sql_artifact.summary,
                    sql_artifact.result_preview or sql_artifact.raw_output,
                    knowledge_artifact.raw_output,
                    current_time,
                )
            )
            artifact = self._build_analysis_artifact_from_payload(payload)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "规则分析失败后的模型兜底也失败，使用模板 fallback",
                thread_id=self.thread_id,
                error=str(exc),
            )
            artifact = structured_analysis.analysis_artifact
        if not artifact.conclusion:
            raise SingleAgentExecutionError("分析阶段未生成结论")
        self._record_artifact("analysis", artifact, stage="analysis")
        return artifact

    async def decide_workorder(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
    ) -> WorkOrderSuggestion:
        suggestion = build_workorder_suggestion(
            request=request,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
            analysis_artifact=analysis_artifact,
        )
        self._record_artifact("workorder_decision", suggestion, stage="workorder_decision")
        return suggestion

    def _build_analysis_artifact_from_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback: AnalysisStepArtifact | None = None,
    ) -> AnalysisStepArtifact:
        def text_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        confidence = str(payload.get("confidence") or "").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = fallback.confidence if fallback is not None else "low"

        conclusion = str(payload.get("conclusion") or "").strip()
        basis = text_list(payload.get("basis"))
        basis = self._sanitize_analysis_basis(basis)
        probable_causes = text_list(payload.get("probable_causes"))
        probable_causes = self._sanitize_analysis_probable_causes(probable_causes)
        verification_items = text_list(payload.get("verification_items"))
        recommendations = text_list(payload.get("recommendations"))
        recommendations = self._sanitize_analysis_recommendations(recommendations)
        verification_items, moved_recommendations = self._sanitize_analysis_verification_items(verification_items)
        recommendations = self._sanitize_analysis_recommendations([*recommendations, *moved_recommendations])
        missing_information = text_list(payload.get("missing_information"))
        missing_information = self._sanitize_analysis_missing_information(missing_information)
        confidence_details = text_list(payload.get("confidence_details"))
        risk_notice = self._sanitize_analysis_text(str(payload.get("risk_notice") or "").strip()) or None

        if fallback is not None:
            conclusion = conclusion or fallback.conclusion
            basis = basis or fallback.basis
            probable_causes = probable_causes or fallback.probable_causes
            verification_items = verification_items or fallback.verification_items
            recommendations = recommendations or fallback.recommendations
            missing_information = missing_information or fallback.missing_information
            confidence_details = confidence_details or fallback.confidence_details
            risk_notice = risk_notice or fallback.risk_notice

        return AnalysisStepArtifact(
            success=True,
            conclusion=conclusion,
            basis=basis,
            probable_causes=probable_causes,
            verification_items=verification_items,
            recommendations=recommendations,
            risk_notice=risk_notice,
            missing_information=missing_information,
            confidence_details=confidence_details,
            confidence=confidence,
            error=None,
        )

    def _sanitize_analysis_recommendations(self, recommendations: list[str]) -> list[str]:
        sanitized: list[str] = []
        for item in recommendations:
            text = str(item or "").strip()
            if not text:
                continue
            text = self._sanitize_analysis_text(text)
            if text not in sanitized:
                sanitized.append(text)
        return sanitized

    def _sanitize_analysis_basis(self, basis: list[str]) -> list[str]:
        sanitized: list[str] = []
        for item in basis:
            text = str(item or "").strip()
            if not text:
                continue
            text = re.sub(
                r"[，,；;]\s*(?:需|需要|建议|应|应当|优先)(?:检查|核对|确认|排查|处理|复核).*$",
                "",
                text,
            ).strip(" 。；;，,")
            if text and text not in sanitized:
                sanitized.append(text)
        return sanitized

    def _sanitize_analysis_probable_causes(self, causes: list[str]) -> list[str]:
        sanitized: list[str] = []
        for item in causes:
            text = self._sanitize_analysis_text(str(item or "").strip())
            if not text:
                continue
            text = re.sub(r"[，,]?\s*(?:导致|引起|造成)([^。；;，,]*)", r"，可能关联\1", text)
            text = re.sub(r"[，,]?\s*影响([^。；;，,]*)", r"，与\1的关系需验证", text)
            text = re.sub(r"；{2,}", "；", text).strip(" 。；;，,")
            if text and text not in sanitized:
                sanitized.append(text)
        return sanitized

    def _sanitize_analysis_verification_items(self, items: list[str]) -> tuple[list[str], list[str]]:
        verification_items: list[str] = []
        moved_recommendations: list[str] = []
        hard_action_re = re.compile(r"^(?:按|记录|复位|恢复|执行|操作|避免|将|观察|重启)")
        check_prefix_re = re.compile(r"^(?:现场)?(?:确认|检查|核对|排查)(?:当前)?")

        for item in items:
            text = self._sanitize_analysis_text(str(item or "").strip())
            if not text:
                continue
            if hard_action_re.match(text):
                moved_recommendations.append(text)
                continue
            normalized = check_prefix_re.sub("", text, count=1).strip(" ：:，,。")
            normalized = normalized or text
            if normalized and normalized not in verification_items:
                verification_items.append(normalized)
        return verification_items, moved_recommendations

    def _sanitize_analysis_missing_information(self, items: list[str]) -> list[str]:
        sanitized: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            text = re.sub(r"^(?:需要|需|待|请)?(?:确认|补充|检查|核对)\s*", "", text).strip(" ：:，,。")
            if text and text not in sanitized:
                sanitized.append(text)
        return sanitized

    def _sanitize_analysis_text(self, text: str) -> str:
        text = re.sub(
            r"降载至\s*\d+(?:\.\d+)?\s*%\s*(?:以下|以内|左右)?",
            "按现场规程降载",
            text,
        )
        text = re.sub(
            r"负载(?:率)?(?:控制|降至|降到)\s*\d+(?:\.\d+)?\s*%\s*(?:以下|以内|左右)?",
            "负载按现场规程控制在安全范围",
            text,
        )
        text = text.replace("避免带载运行", "避免在参数或功能块状态未确认前继续带载试运行")
        return text

    async def stream_report_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
        workorder_suggestion: WorkOrderSuggestion | None,
        current_time: str,
    ) -> AsyncGenerator[str, None]:
        report_filename = f"dcma_single_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.thread_id[-6:]}"
        payload = build_report_payload(
            request=request,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
            analysis_artifact=analysis_artifact,
            current_time=current_time,
            report_filename=report_filename,
            workorder_suggestion=workorder_suggestion,
        )
        async for chunk in self._invoke_restricted_tool(
            tool_name="save_report",
            tool=get_report_tool(),
            tool_input=payload,
            stage="report",
        ):
            yield chunk
        save_result = stringify(self._last_step_result)
        artifact = ReportStepArtifact(
            success="失败" not in save_result,
            report_filename=extract_report_filename(save_result, f"{report_filename}.html"),
            report_title=str(payload.get("title") or "").strip() or None,
            report_url=extract_report_url(save_result),
            save_result=save_result,
            error=None if "失败" not in save_result else save_result,
        )
        self._record_artifact("report", artifact, stage="report")
        self._last_step_result = artifact

    async def stream_report_from_previous_artifact(self) -> AsyncGenerator[str, None]:
        envelope = get_thread_artifact(self.thread_id)
        if envelope is None:
            raise SingleAgentExecutionError("当前线程没有可用于生成报告的结构化结果")
        report_payload = map_artifact_to_report_payload(envelope)
        async for chunk in self._invoke_restricted_tool(
            tool_name="save_report",
            tool=get_report_tool(),
            tool_input=report_payload,
            stage="report",
        ):
            yield chunk
        save_result = stringify(self._last_step_result)
        artifact = ReportStepArtifact(
            success="失败" not in save_result,
            report_filename=extract_report_filename(save_result, report_payload.get("report_filename")),
            report_title=str(report_payload.get("title") or "").strip() or None,
            report_url=extract_report_url(save_result),
            save_result=save_result,
            error=None if "失败" not in save_result else save_result,
        )
        self._record_artifact("report", artifact, stage="report")
        source_name = (
            "故障诊断结果"
            if str(envelope.workflow_type) == DiagnosisArtifactType.FAULT_DIAGNOSIS.value
            else "结构化结果"
        )
        payload = envelope.payload or {}
        analysis_payload = payload.get("analysis_artifact") or {}
        summary_items = [
            str(analysis_payload.get("conclusion") or envelope.request_summary or "").strip(),
            *[str(item).strip() for item in (analysis_payload.get("basis") or []) if str(item).strip()],
            *[str(item).strip() for item in (analysis_payload.get("recommendations") or []) if str(item).strip()],
        ]
        summary_lines = [
            f"{index}. {item}"
            for index, item in enumerate(list(dict.fromkeys(summary_items))[:5], start=1)
            if item
        ] or ["1. 已基于当前线程保存的结构化结果生成报告。"]
        report_link = artifact.report_url or artifact.report_filename or "未返回报告链接"
        report_title = artifact.report_title or artifact.report_filename or "诊断报告"
        final_answer = (
            f"报告状态：已基于当前线程最近一次{source_name}生成报告。\n\n"
            f"报告标题：{report_title}\n\n"
            f"报告摘要：\n{chr(10).join(summary_lines)}\n\n"
            f"报告链接：{report_link}\n\n"
            "证据不足提示：本报告基于已保存的结构化结果生成，若现场状态或数据窗口已变化，需要重新诊断后确认。"
        )
        self._last_step_result = final_answer, artifact

    async def build_final_answer(
        self,
        analysis_artifact: AnalysisStepArtifact,
        report_artifact: ReportStepArtifact,
        decision: SingleAgentDecision | None = None,
        sql_artifact: SqlStepArtifact | None = None,
        knowledge_artifact: KnowledgeStepArtifact | None = None,
        workorder_suggestion: WorkOrderSuggestion | None = None,
    ) -> str:
        if decision is None:
            decision = SingleAgentDecision()

        rendered_answer = build_templated_final_answer(
            decision=decision,
            evidence_bundle=self.evidence_bundle,
            analysis_artifact=analysis_artifact,
            workorder_suggestion=workorder_suggestion,
            report_artifact=report_artifact,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
        )
        self._last_rendered_answer = rendered_answer
        self._record_artifact("rendered_answer", rendered_answer, stage="final_answer")
        answer = rendered_answer.content

        prefixes: list[str] = []
        authorization = decision.authorization or {}
        if authorization.get("mode") == "degrade":
            prefixes.append(
                "【权限范围】当前身份只能查看 real_data_01 最近一小时数据和公开处理意见；"
                "以下内容不是故障诊断、根因判断或健康评估。"
            )
        elif self.auth_context.role == "guest" and decision.primary_task_type == "alarm_triage":
            prefixes.append("【权限范围】当前身份仅提供公开故障码说明、处理意见和最近一小时数据现状。")
        if decision.primary_task_type == "action_request":
            prefixes.append(
                "【动作审批】本次请求识别为写操作/控制操作意图；"
                "Agent 不直接执行设备控制、配置修改、告警关闭或工单派发，"
                "只能提供草稿、审批提示或人工确认建议。"
            )
        blocked_subgoals = [
            item for item in decision.subgoals if item.get("status") == "blocked" and item.get("missing_slots")
        ]
        if blocked_subgoals:
            missing = []
            for item in blocked_subgoals[:3]:
                missing.extend(str(slot) for slot in item.get("missing_slots") or [])
            unique_missing = list(dict.fromkeys(missing))
            prefixes.append(f"【待补充】{'; '.join(unique_missing[:5])}。这些缺口会降低对应子目标结论置信度。")
        if prefixes:
            answer = "\n".join([*prefixes, answer])
            rendered_answer.content = answer
            return answer
        return answer

    def _build_skipped_sql_artifact(self, reason: str) -> SqlStepArtifact:
        artifact = SqlStepArtifact(
            success=False,
            summary=reason,
            sql_used=[],
            result_preview="",
            raw_output="",
            error=None,
        )
        self._record_artifact("sql", artifact, stage="sql")
        return artifact

    def _build_skipped_knowledge_artifact(self, reason: str) -> KnowledgeStepArtifact:
        artifact = KnowledgeStepArtifact(
            success=False,
            query="",
            snippets=[],
            raw_output="",
            error=reason,
        )
        self._record_artifact("knowledge", artifact, stage="knowledge")
        return artifact

    def _build_skipped_report_artifact(self) -> ReportStepArtifact:
        artifact = ReportStepArtifact(
            success=False,
            report_filename=None,
            report_title=None,
            report_url=None,
            save_result="本次请求未要求生成报告",
            error=None,
        )
        self._record_artifact("report", artifact, stage="report")
        return artifact

    def _build_skipped_workorder_suggestion(self, reason: str) -> WorkOrderSuggestion:
        suggestion = WorkOrderSuggestion(
            need_workorder=False,
            reason=reason,
            workorder_type="",
            priority="P3",
            priority_label="未触发",
            risk_level="低",
        )
        self._record_artifact("workorder_decision", suggestion, stage="workorder_decision")
        return suggestion

    def save_artifact_envelope(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
        workorder_suggestion: WorkOrderSuggestion,
        report_artifact: ReportStepArtifact,
        final_answer: str,
        decision: SingleAgentDecision,
        evidence_bundle: EvidenceBundle | None = None,
        output_guardrail: dict[str, object] | None = None,
        rendered_answer: Any | None = None,
        workflow_artifacts: dict[str, object] | None = None,
    ) -> DiagnosisArtifactEnvelope:
        self.trace.add_event(
            "artifact",
            stage="final_answer",
            status="created",
            artifact_type="diagnosis_artifact",
            artifact={"workflow_type": DiagnosisArtifactType.FAULT_DIAGNOSIS.value, "thread_id": self.thread_id},
        )
        envelope = build_diagnosis_artifact_envelope(
            thread_id=self.thread_id,
            request=request,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
            analysis_artifact=analysis_artifact,
            workorder_suggestion=workorder_suggestion,
            report_artifact=report_artifact,
            final_answer=final_answer,
            decision=decision,
            trace=self.trace,
            evidence_bundle=evidence_bundle,
            output_guardrail=output_guardrail,
            rendered_answer=rendered_answer,
            workflow_artifacts=workflow_artifacts,
            auth=self.auth_context.audit_summary(),
            authorization=decision.authorization,
        )
        return save_thread_artifact(envelope)
