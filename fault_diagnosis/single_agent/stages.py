"""Business stage handlers for the restricted single-agent pipeline."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, AsyncGenerator

from ..common.logger import get_logger
from ..diagnosis.adapters import build_sql_tools_map, find_sql_tool
from ..diagnosis.artifact_store import get_thread_artifact, save_thread_artifact
from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
)
from ..diagnosis.report_mapper import map_artifact_to_report_payload
from ..diagnosis.steps import (
    build_default_knowledge_query,
    build_knowledge_artifact,
    build_request_from_payload,
    build_sql_plan,
)
from ..diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text
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
from .final_answer import build_final_answer_fallback
from .prompts import (
    build_single_agent_analysis_prompt,
    build_single_agent_evidence_synthesis_prompt,
    build_single_agent_understanding_prompt,
)
from .reporting import (
    build_analysis_evidence_summary,
    build_structured_analysis_artifact,
    build_report_payload,
    extract_report_filename,
    extract_report_url,
)
from .serialization import preview, stringify
from .sql_safety import build_fallback_sql_query, build_sql_prompt, has_unknown_sql_table, is_readonly_sql
from .sql_safety import build_fast_sql_plan
from .tool_access import get_knowledge_tool, get_report_tool

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
                sql_query = checked_query_text

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
        structured_artifact = build_structured_analysis_artifact(
            request=request,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
        )
        if structured_artifact is not None:
            evidence_summary = build_analysis_evidence_summary(
                request=request,
                sql_artifact=sql_artifact,
                knowledge_artifact=knowledge_artifact,
            )
            try:
                payload = await self._invoke_json_model(
                    build_single_agent_evidence_synthesis_prompt(
                        request,
                        evidence_summary,
                        structured_artifact.conclusion,
                        structured_artifact.basis,
                        structured_artifact.recommendations,
                        current_time,
                    )
                )
                artifact = self._build_analysis_artifact_from_payload(payload, fallback=structured_artifact)
                self._record_artifact("analysis", artifact, stage="analysis")
                return artifact
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "诊断证据合成模型失败，使用结构化规则结果",
                    thread_id=self.thread_id,
                    error=str(exc),
                )
            self._record_artifact("analysis", structured_artifact, stage="analysis")
            return structured_artifact

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
        if not artifact.conclusion:
            raise SingleAgentExecutionError("分析阶段未生成结论")
        self._record_artifact("analysis", artifact, stage="analysis")
        return artifact

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
            save_result=save_result,
            error=None if "失败" not in save_result else save_result,
        )
        self._record_artifact("report", artifact, stage="report")
        source_name = (
            "故障诊断结果"
            if str(envelope.workflow_type) == DiagnosisArtifactType.FAULT_DIAGNOSIS.value
            else "结构化结果"
        )
        final_answer = (
            f"已基于当前线程最近一次{source_name}生成报告。\n"
            f"【来源摘要】{envelope.request_summary}\n"
            f"【报告文件】{extract_report_url(save_result) or artifact.report_filename or '未生成'}\n"
            f"【保存结果】{save_result}"
        )
        self._last_step_result = final_answer, artifact

    async def build_final_answer(
        self,
        analysis_artifact: AnalysisStepArtifact,
        report_artifact: ReportStepArtifact,
    ) -> str:
        report_name = (
            report_artifact.report_filename
            if report_artifact and report_artifact.report_filename
            else None
        )
        return build_final_answer_fallback(analysis_artifact, report_name)

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
            save_result="本次请求未要求生成报告",
            error=None,
        )
        self._record_artifact("report", artifact, stage="report")
        return artifact

    def save_artifact_envelope(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
        report_artifact: ReportStepArtifact,
        final_answer: str,
        decision: SingleAgentDecision,
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
            report_artifact=report_artifact,
            final_answer=final_answer,
            decision=decision,
            trace=self.trace,
        )
        return save_thread_artifact(envelope)
