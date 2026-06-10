"""故障诊断场景 Runner。"""

from __future__ import annotations

import ast
import json
import os
import re
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
    AnalysisStepArtifact,
    DiagnosisRequest,
    EvidenceItem,
    KnowledgeStepArtifact,
    PlanningArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkflowArtifactEnvelope,
    WorkflowRunResult,
    WorkflowType,
)
from ..prompts import (
    build_analysis_prompt,
    build_final_answer_prompt,
    build_sql_generation_prompt,
    build_understanding_prompt,
)
from ..steps import (
    build_default_knowledge_query,
    build_knowledge_artifact,
    build_sql_plan,
    execute_sql_plan,
    parse_request_from_prompt,
)
from .base import BaseScenarioRunner

_log = get_logger("workflow_runner")
_workflow_model = None
_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+`?([a-zA-Z_][\w]*)`?", re.IGNORECASE)
_FAULT_SQL_ALLOWED_TABLES = {"real_data", "device_alarm", "device_metric", "device_fault_data", "fault_records"}
_FAULT_DIAGNOSIS_SQL_SCHEMA_CONTEXT = """
仅允许使用以下 MySQL 表，不要使用未列出的表名：
- real_data(timestamp, device_name, device_id, fault_code, spindle_current, spindle_speed, spindle_load, motor_temp, vibration, alarm_status)
- device_alarm(timestamp, alarm_time, device_name, device_id, alarm_code, fault_code, alarm_level, alarm_message, status)
- device_metric(device_id, metric_name, metric_value, record_time)
- device_fault_data(event_time, device_name, device_id, fault_code, spindle_load, vibration, motor_temperature, motor_temp, spindle_current, spindle_speed, alarm_status)
- fault_records(fault_code, description, possible_cause, suggestion, severity)
主轴负载、振动、电机温度优先从 real_data 的 spindle_load、vibration、motor_temp 查询。
""".strip()


class WorkflowExecutionError(Exception):
    """Workflow 执行异常。"""


def _get_workflow_model():
    """延迟创建 Workflow 专用模型。"""

    global _workflow_model
    if _workflow_model is None:
        from langchain_openai import ChatOpenAI

        _workflow_model = ChatOpenAI(
            model=os.getenv("MODEL_NAME"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.2,
        )
    return _workflow_model


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


def _preview(value: Any, limit: int = 800) -> str:
    text = _stringify(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _extract_json_text(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        raise WorkflowExecutionError("模型未返回有效 JSON")
    block_match = _JSON_BLOCK_RE.search(stripped)
    if block_match:
        return block_match.group(1).strip()
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        return stripped[first_brace : last_brace + 1]
    raise WorkflowExecutionError("模型返回内容中未找到 JSON 对象")


def _loads_json_object(text: str) -> dict[str, Any]:
    parse_errors: list[str] = []
    for parser_name, parser in (("json", json.loads), ("literal", ast.literal_eval)):
        try:
            payload = parser(text)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{parser_name}: {exc}")
            continue
        if isinstance(payload, dict):
            return payload
        parse_errors.append(f"{parser_name}: 返回值不是 JSON 对象")
    raise WorkflowExecutionError("模型 JSON 解析失败：" + "；".join(parse_errors))


def _build_json_repair_prompt(raw_text: str, error_message: str) -> str:
    return f"""
你是 JSON 修复器。
下面内容原本应该是一个 JSON 对象，但解析失败。
请只输出修复后的 JSON 对象，不要输出解释、Markdown 或代码块。

解析错误：{error_message}

待修复内容：
{_preview(raw_text, 6000)}
""".strip()


def _extract_sql_table_names(sql_query: str) -> set[str]:
    return {match.group(1).lower() for match in _SQL_TABLE_RE.finditer(sql_query or "")}


def _has_unknown_sql_table(sql_query: str) -> bool:
    table_names = _extract_sql_table_names(sql_query)
    return any(table_name not in _FAULT_SQL_ALLOWED_TABLES for table_name in table_names)


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _build_fallback_fault_sql_query(request: DiagnosisRequest) -> str:
    equipment_hint = (request.equipment_hint or "SPINDLE-01").strip()
    fault_code_hint = (request.fault_code_hint or "").strip()
    conditions = []
    if equipment_hint:
        equipment_literal = _sql_literal(equipment_hint)
        conditions.append(f"(device_id = {equipment_literal} OR device_name = {equipment_literal})")
    if fault_code_hint:
        fault_code_literal = _sql_literal(fault_code_hint)
        conditions.append(f"(fault_code = {fault_code_literal} OR fault_code IS NULL)")
    where_clause = " AND ".join(conditions) or "1=1"
    return (
        "SELECT timestamp, device_name, device_id, fault_code, spindle_current, spindle_speed, "
        "spindle_load, motor_temp, vibration, alarm_status "
        f"FROM real_data WHERE {where_clause} ORDER BY timestamp DESC LIMIT 50"
    )


async def _invoke_text_model(prompt: str) -> str:
    response = await _get_workflow_model().ainvoke(prompt)
    return getattr(response, "content", "") or ""


async def _invoke_json_model(prompt: str) -> dict[str, Any]:
    raw_text = await _invoke_text_model(prompt)
    try:
        return _loads_json_object(_extract_json_text(raw_text))
    except WorkflowExecutionError as exc:
        repaired_text = await _invoke_text_model(_build_json_repair_prompt(raw_text, str(exc)))
        try:
            return _loads_json_object(_extract_json_text(repaired_text))
        except WorkflowExecutionError as repair_exc:
            raise WorkflowExecutionError(
                f"模型 JSON 解析失败：{repair_exc}；原始错误：{exc}；原始响应预览：{_preview(raw_text, 500)}"
            ) from repair_exc


class FaultDiagnosisRunner(BaseScenarioRunner):
    """承接第一期主链路的故障诊断场景 Runner。"""

    def build_evidence_items(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
    ) -> list[EvidenceItem]:
        """构建故障诊断流的结构化证据。"""

        evidence: list[EvidenceItem] = [
            EvidenceItem(
                source_type="sql",
                title="SQL 查询摘要",
                content=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
                importance="high",
            ),
            EvidenceItem(
                source_type="knowledge_base",
                title="知识检索摘要",
                content=knowledge_artifact.raw_output or "无可靠知识检索结果",
                importance="medium" if knowledge_artifact.success else "low",
            ),
            EvidenceItem(
                source_type="analysis",
                title="诊断结论",
                content=analysis_artifact.conclusion,
                importance="high",
            ),
        ]
        if analysis_artifact.basis:
            evidence.append(
                EvidenceItem(
                    source_type="analysis",
                    title="诊断依据",
                    content="；".join(analysis_artifact.basis),
                    importance="high",
                )
            )
        if request.fault_code_hint:
            evidence.append(
                EvidenceItem(
                    source_type="analysis",
                    title="故障码提示",
                    content=request.fault_code_hint,
                    importance="medium",
                )
            )
        return evidence

    def save_artifact_envelope(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
        report_artifact: ReportStepArtifact | None,
        final_answer: str,
        planning_artifact: PlanningArtifact | None = None,
    ) -> WorkflowArtifactEnvelope:
        """保存故障诊断流结构化产物。"""

        evidence_bundle = build_workflow_evidence_bundle(
            route_result=self._route_payload(),
            finding_text=analysis_artifact.conclusion,
            confidence=analysis_artifact.confidence,
            has_sql=sql_artifact.success,
            sql_title="Workflow SQL 结果",
            sql_summary=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
            sql_query="; ".join(sql_artifact.sql_used or []),
            has_knowledge=knowledge_artifact.success,
            knowledge_title="Workflow 知识检索结果",
            knowledge_summary=knowledge_artifact.raw_output,
            knowledge_query=knowledge_artifact.query,
            knowledge_required=True,
        )
        governance = evidence_bundle["governance"]

        envelope = WorkflowArtifactEnvelope(
            workflow_type=WorkflowType.FAULT_DIAGNOSIS,
            thread_id=self.thread_id,
            created_at=self._iso_now(),
            request_summary=request.analysis_goal or request.user_message,
            final_answer=final_answer,
            report_filename=report_artifact.report_filename if report_artifact else None,
            payload={
                "request": request.model_dump(exclude_none=True),
                "sql_artifact": sql_artifact.model_dump(exclude_none=True),
                "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
                "analysis_artifact": analysis_artifact.model_dump(exclude_none=True),
                "report_artifact": report_artifact.model_dump(exclude_none=True) if report_artifact else None,
                "planning": planning_artifact.model_dump(exclude_none=True) if planning_artifact else None,
                "route_result": self._route_payload(),
                "governance": governance,
                "report_gate_summary": evidence_bundle["report_gate_summary"],
                "findings_snapshot": evidence_bundle["findings_snapshot"],
                "finding_links_snapshot": evidence_bundle["finding_links_snapshot"],
                "evidence_records_snapshot": evidence_bundle["evidence_records_snapshot"],
            },
            evidence=self.build_evidence_items(request, sql_artifact, knowledge_artifact, analysis_artifact),
        )
        return save_thread_artifact(envelope)

    async def parse_request(self) -> DiagnosisRequest:
        started_at = self._iso_now()
        prompt = build_understanding_prompt(self.message, self.user_identity)
        try:
            request = await parse_request_from_prompt(
                self.message,
                self.user_identity,
                prompt,
                _invoke_json_model,
                needs_report=True,
            )
            self._record_step(
                step_name="parse_request",
                status="success",
                summary="已完成请求理解",
                started_at=started_at,
            )
            return request
        except Exception as exc:
            self._record_step(
                step_name="parse_request",
                status="error",
                summary="请求理解失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"请求理解失败：{exc}") from exc

    async def build_planning_artifact(self, request: DiagnosisRequest) -> PlanningArtifact:
        """生成故障诊断流的执行前计划。"""

        started_at = self._iso_now()
        try:
            planning_artifact = await create_planning_artifact(
                request.user_message or self.message,
                request.user_identity or self.user_identity,
                self.route_result,
            )
        except Exception as exc:  # noqa: BLE001
            planning_artifact = build_default_plan(
                request.user_message or self.message,
                request.user_identity or self.user_identity,
                self.route_result,
            )
            planning_artifact.fallback_used = True
            planning_artifact.error = f"planner 接入异常，已回退规则计划：{exc}"
        self._record_step(
            step_name="planning",
            status="warning" if planning_artifact.fallback_used else "success",
            summary="已生成 planner 结构化计划",
            started_at=started_at,
            error=planning_artifact.error,
        )
        return planning_artifact

    async def run_sql_step(self, request: DiagnosisRequest) -> SqlStepArtifact:
        started_at = self._iso_now()
        try:
            sql_query, summary = await build_sql_plan(
                build_sql_generation_prompt(request, _FAULT_DIAGNOSIS_SQL_SCHEMA_CONTEXT),
                _invoke_json_model,
                default_summary="已生成 SQL 查询",
            )
            if not sql_query:
                raise WorkflowExecutionError("SQL 生成结果为空")
            if _has_unknown_sql_table(sql_query):
                sql_query = _build_fallback_fault_sql_query(request)
                summary = "已改用 real_data 查询设备故障前后的主轴负载、振动和电机温度数据"

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
                summary="SQL 查询完成",
                started_at=started_at,
            )
            return artifact
        except Exception as exc:
            self._record_step(
                step_name="sql",
                status="error",
                summary="SQL 查询失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"SQL 查询失败：{exc}") from exc

    async def run_knowledge_step(self, request: DiagnosisRequest, sql_artifact: SqlStepArtifact) -> KnowledgeStepArtifact:
        del sql_artifact
        started_at = self._iso_now()
        query = build_default_knowledge_query(request)
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
                summary="知识检索完成" if artifact.success else "知识检索未命中或结果不足",
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
                summary="知识检索失败，后续将按有限依据继续分析",
                started_at=started_at,
                error=str(exc),
            )
            return artifact

    async def run_analysis_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        current_time: str,
        planning_artifact: PlanningArtifact | None = None,
    ) -> AnalysisStepArtifact:
        started_at = self._iso_now()
        try:
            payload = await _invoke_json_model(
                build_analysis_prompt(request, sql_artifact, knowledge_artifact, current_time, planning_artifact)
            )
            artifact = AnalysisStepArtifact(
                success=True,
                conclusion=str(payload.get("conclusion") or "").strip(),
                basis=[str(item).strip() for item in (payload.get("basis") or []) if str(item).strip()],
                recommendations=[
                    str(item).strip() for item in (payload.get("recommendations") or []) if str(item).strip()
                ],
                risk_notice=(str(payload.get("risk_notice")).strip() if payload.get("risk_notice") else None),
                missing_information=[
                    str(item).strip() for item in (payload.get("missing_information") or []) if str(item).strip()
                ],
                confidence=str(payload.get("confidence") or "low").strip().lower() or "low",
                error=None,
            )
            if not artifact.conclusion:
                raise WorkflowExecutionError("分析阶段未生成结论")
            self._record_step(
                step_name="analysis",
                status="success",
                summary="诊断分析完成",
                started_at=started_at,
            )
            return artifact
        except Exception as exc:
            self._record_step(
                step_name="analysis",
                status="error",
                summary="诊断分析失败",
                started_at=started_at,
                error=str(exc),
            )
            raise WorkflowExecutionError(f"诊断分析失败：{exc}") from exc

    async def run_report_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
        current_time: str,
    ) -> ReportStepArtifact:
        started_at = self._iso_now()
        if not request.needs_report:
            artifact = ReportStepArtifact(
                success=False,
                report_filename=None,
                save_result="本次请求无需生成报告",
                error=None,
            )
            self._record_step(
                step_name="report",
                status="skipped",
                summary="本次请求未生成报告",
                started_at=started_at,
            )
            return artifact

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"dcma_workflow_v1_{timestamp}_{self.thread_id[-6:]}"
        try:
            evidence_bundle = build_workflow_evidence_bundle(
                route_result=self._route_payload(),
                finding_text=analysis_artifact.conclusion,
                confidence=analysis_artifact.confidence,
                has_sql=sql_artifact.success,
                sql_title="Workflow SQL 结果",
                sql_summary=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
                sql_query="; ".join(sql_artifact.sql_used or []),
                has_knowledge=knowledge_artifact.success,
                knowledge_title="Workflow 知识检索结果",
                knowledge_summary=knowledge_artifact.raw_output,
                knowledge_query=knowledge_artifact.query,
                knowledge_required=True,
            )
            save_result = save_markdown_report_from_analysis(
                title="DCMA 工作流 V1 诊断报告",
                report_time=current_time,
                diagnosis_object=request.equipment_hint or "DCMA 系统",
                diagnosis_type=request.fault_code_hint or "运行诊断",
                executive_summary=analysis_artifact.conclusion,
                diagnosis_overview="本报告由 Workflow V1 主链路生成，依次完成请求理解、SQL 查询、知识检索、诊断分析和报告保存。",
                diagnosis_details=(
                    f"【SQL 结果摘要】\n{sql_artifact.result_preview or sql_artifact.raw_output or '无'}\n\n"
                    f"【知识检索摘要】\n{knowledge_artifact.raw_output or '无'}"
                ),
                fault_inference=analysis_artifact.conclusion,
                repair_recommendations="\n".join(f"- {item}" for item in analysis_artifact.recommendations)
                or "- 暂无具体处置建议",
                preventive_maintenance="本期未生成专项预防性维护建议。",
                diagnosis_basis=(
                    f"SQL 摘要：{sql_artifact.summary}\n"
                    f"SQL 语句：{'; '.join(sql_artifact.sql_used) or '无'}\n"
                    f"知识查询：{knowledge_artifact.query}\n"
                    f"分析依据：{'; '.join(analysis_artifact.basis) or '无'}"
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
                summary="Markdown 报告已生成",
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
                summary="报告生成失败，保留分析结果",
                started_at=started_at,
                error=str(exc),
            )
            return artifact

    async def build_final_answer(
        self,
        analysis_artifact: AnalysisStepArtifact,
        report_artifact: ReportStepArtifact | None,
        planning_artifact: PlanningArtifact | None = None,
    ) -> str:
        prompt = build_final_answer_prompt(analysis_artifact, report_artifact, planning_artifact)
        try:
            final_answer = (await _invoke_text_model(prompt)).strip()
            if final_answer:
                return final_answer
        except Exception as exc:
            _log.warning("最终答复整理失败，回退到模板输出", error=str(exc))

        basis_lines = "\n".join(f"- {item}" for item in analysis_artifact.basis) or "- 暂无明确数据支撑"
        recommendation_lines = (
            "\n".join(f"- {item}" for item in analysis_artifact.recommendations) or "- 暂无具体处置建议"
        )
        risk_notice = analysis_artifact.risk_notice or "当前未发现额外风险提示。"
        report_name = report_artifact.report_filename if report_artifact and report_artifact.report_filename else "未生成"
        return (
            f"【结论】{analysis_artifact.conclusion}\n"
            f"【数据支撑】\n{basis_lines}\n"
            f"【处置建议】\n{recommendation_lines}\n"
            f"【风险提示】{risk_notice}\n"
            f"【报告文件】{report_name}"
        )

    async def run(self) -> WorkflowRunResult:
        request = await self.parse_request()
        planning_artifact = await self.build_planning_artifact(request)
        current_time = get_current_time_text()
        sql_artifact = await self.run_sql_step(request)
        knowledge_artifact = await self.run_knowledge_step(request, sql_artifact)
        analysis_artifact = await self.run_analysis_step(
            request,
            sql_artifact,
            knowledge_artifact,
            current_time,
            planning_artifact,
        )
        report_artifact = await self.run_report_step(
            request,
            sql_artifact,
            knowledge_artifact,
            analysis_artifact,
            current_time,
        )
        final_answer = await self.build_final_answer(analysis_artifact, report_artifact, planning_artifact)
        self.save_artifact_envelope(
            request,
            sql_artifact,
            knowledge_artifact,
            analysis_artifact,
            report_artifact,
            final_answer,
            planning_artifact,
        )
        return WorkflowRunResult(
            final_answer=final_answer,
            steps=self.steps,
            request=request,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
            analysis_artifact=analysis_artifact,
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
        """按现有 SSE 契约输出故障诊断流结果。"""

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
                    "message": "Workflow V1 已开始执行，正在整理请求并准备诊断链路。",
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

            yield _emit(
                "tool_start",
                {"type": "tool_start", "tool": "query_knowledge_base", "input": {"query": request.fault_code_hint or request.analysis_goal}},
            )
            event_count += 1
            async for ping in self._drive_step(self.run_knowledge_step(request, sql_artifact), stage="tool_call"):
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
                self.run_analysis_step(request, sql_artifact, knowledge_artifact, current_time, planning_artifact),
                stage="reasoning",
            ):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            analysis_artifact = self._last_step_result

            yield _emit(
                "tool_start",
                {"type": "tool_start", "tool": "save_report", "input": {"report_format": request.report_format}},
            )
            event_count += 1
            async for ping in self._drive_step(
                self.run_report_step(request, sql_artifact, knowledge_artifact, analysis_artifact, current_time),
                stage="tool_call",
            ):
                yield ping
            if self._is_cancelled():
                yield self._build_cancel_complete_frame()
                return
            report_artifact = self._last_step_result
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
                self.build_final_answer(analysis_artifact, report_artifact, planning_artifact),
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
                knowledge_artifact,
                analysis_artifact,
                report_artifact,
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
                "governance": (saved_envelope.payload or {}).get("governance", {}),
                "todos": current_todos,
                "event_count": event_count,
                "timestamp": datetime.now().isoformat(),
            }
            yield _emit("complete", completion_data)
            _log.info(
                "Workflow V1 流式请求完成",
                thread_id=self.thread_id,
                stream_id=stream_id,
                event_count=event_count,
                token_count=token_count,
                duration_ms=round((time.monotonic() - stream_started_at) * 1000, 1),
            )
        except Exception as exc:
            _log.exception("Workflow V1 流式请求失败", thread_id=self.thread_id, stream_id=stream_id, error=str(exc))
            error_id = request_id or f"workflow-{int(time.time())}"
            yield _emit(
                "server_error",
                self._build_server_error_payload(error_id=error_id, error=exc),
            )
