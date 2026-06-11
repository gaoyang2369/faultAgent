"""Restricted single-agent runner for the minimal diagnosis path."""

from __future__ import annotations

import asyncio
import ast
import contextlib
import json
import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncGenerator

from ..agent_runtime.error_classification import classify_model_gateway_error, model_error_code
from ..agent_runtime.sse_adapter import build_server_error_payload, encode_sse_event
from ..common.logger import get_logger
from ..workflows.adapters import build_sql_tools_map, find_sql_tool, invoke_tool
from ..workflows.artifact_store import get_thread_artifact, save_thread_artifact
from ..workflows.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    EvidenceItem,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkflowArtifactEnvelope,
    WorkflowType,
)
from ..workflows.report_mapper import map_artifact_to_report_payload
from ..workflows.steps import build_default_knowledge_query, build_knowledge_artifact, build_request_from_payload, build_sql_plan
from .contracts import AgentTrace, SingleAgentDecision, SingleAgentLimits
from .prompts import build_single_agent_analysis_prompt, build_single_agent_understanding_prompt

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger("single_agent.runner")

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+`?([a-zA-Z_][\w]*)`?", re.IGNORECASE)
_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9._\-]+\.(?:md|html))", re.IGNORECASE)
_FAULT_CODE_RE = re.compile(r"\b([A-Z]\d{4,})\b", re.IGNORECASE)
_DEVICE_RE = re.compile(r"\b([A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机)\b", re.IGNORECASE)

_ALLOWED_SQL_TABLES = {"real_data", "device_alarm", "device_metric", "device_fault_data", "fault_records"}
_SQL_SCHEMA_CONTEXT = """
仅允许使用以下 MySQL 表，不要使用未列出的表名：
- real_data(timestamp, device_name, device_id, fault_code, spindle_current, spindle_speed, spindle_load, motor_temp, vibration, alarm_status)
- device_alarm(timestamp, alarm_time, device_name, device_id, alarm_code, fault_code, alarm_level, alarm_message, status)
- device_metric(device_id, metric_name, metric_value, record_time)
- device_fault_data(event_time, device_name, device_id, fault_code, spindle_load, vibration, motor_temperature, motor_temp, spindle_current, spindle_speed, alarm_status)
- fault_records(fault_code, description, possible_cause, suggestion, severity)
主轴负载、振动、电机温度优先从 real_data 的 spindle_load、vibration、motor_temp 查询。
只允许生成单条只读 SELECT 查询。
""".strip()

_REPORT_KEYWORDS = ("报告", "出报告", "生成报告", "导出报告", "整理成报告", "形成报告")
_REPORT_CONTEXT_HINTS = ("刚才", "刚刚", "上一轮", "上一条", "上一次", "前面的结果", "诊断结果", "巡检结果")
_SQL_KEYWORDS = (
    "设备",
    "机台",
    "产线",
    "故障",
    "报警",
    "告警",
    "异常",
    "状态",
    "当前",
    "最近",
    "历史",
    "数据",
    "趋势",
    "温度",
    "振动",
    "电流",
    "转速",
    "负载",
)
_KNOWLEDGE_KEYWORDS = (
    "故障码",
    "原因",
    "根因",
    "怎么处理",
    "如何处理",
    "处置",
    "维修",
    "排查",
    "手册",
    "说明",
    "步骤",
    "含义",
    "是什么意思",
)


class SingleAgentExecutionError(Exception):
    """Raised when the restricted single-agent runtime cannot continue."""


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(_sanitize_for_json(value), ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


def _sanitize_for_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _sanitize_for_json(value.model_dump())
    if isinstance(value, dict):
        return {str(key): _sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_json(item) for item in value]
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)


def _preview(value: Any, limit: int = 800) -> str:
    text = _stringify(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _extract_json_text(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        raise SingleAgentExecutionError("模型未返回有效 JSON")
    block_match = _JSON_BLOCK_RE.search(stripped)
    if block_match:
        return block_match.group(1).strip()
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        return stripped[first_brace : last_brace + 1]
    raise SingleAgentExecutionError("模型返回内容中未找到 JSON 对象")


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
    raise SingleAgentExecutionError("模型 JSON 解析失败：" + "；".join(parse_errors))


def _build_json_repair_prompt(raw_text: str, error_message: str) -> str:
    return f"""
你是 JSON 修复器。
下面内容原本应该是一个 JSON 对象，但解析失败。
请只输出修复后的 JSON 对象，不要输出解释、Markdown 或代码块。

解析错误：{error_message}

待修复内容：
{_preview(raw_text, 6000)}
""".strip()


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def _looks_like_report_handoff(message: str) -> bool:
    normalized = (message or "").strip()
    if not _has_any(normalized, _REPORT_KEYWORDS):
        return False
    if _has_any(normalized, _REPORT_CONTEXT_HINTS):
        return True
    compact = normalized.replace(" ", "")
    return compact in {"报告", "出报告", "生成报告", "导出报告", "整理成报告"}


def _extract_report_url(save_result: str) -> str | None:
    matched = _REPORT_URL_RE.search(save_result or "")
    return matched.group(1) if matched else None


def _extract_report_filename(save_result: str, fallback: str | None = None) -> str | None:
    report_url = _extract_report_url(save_result)
    if report_url:
        return report_url.split("/")[-1]
    return fallback


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _extract_sql_table_names(sql_query: str) -> set[str]:
    return {match.group(1).lower() for match in _SQL_TABLE_RE.finditer(sql_query or "")}


def _has_unknown_sql_table(sql_query: str) -> bool:
    table_names = _extract_sql_table_names(sql_query)
    return any(table_name not in _ALLOWED_SQL_TABLES for table_name in table_names)


def _is_readonly_sql(sql_query: str) -> bool:
    normalized = (sql_query or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("select") or normalized.startswith("with")


def _build_fallback_sql_query(request: DiagnosisRequest) -> str:
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


def get_knowledge_tool():
    from ..tools.kb_tools import query_knowledge_base

    return query_knowledge_base


def get_report_tool():
    from ..tools.report_tools import save_report

    return save_report


def _fallback_understanding_payload(message: str, user_identity: str) -> dict[str, Any]:
    fault_code_match = _FAULT_CODE_RE.search(message or "")
    device_match = _DEVICE_RE.search(message or "")
    normalized = (message or "").strip()
    return {
        "user_message": normalized,
        "user_identity": user_identity,
        "equipment_hint": device_match.group(1) if device_match else None,
        "metric_hint": None,
        "fault_code_hint": fault_code_match.group(1).upper() if fault_code_match else None,
        "time_range_hint": "最近" if "最近" in normalized or "当前" in normalized else None,
        "analysis_goal": normalized or "故障诊断",
        "needs_sql": _has_any(normalized, _SQL_KEYWORDS),
        "needs_knowledge": bool(fault_code_match) or _has_any(normalized, _KNOWLEDGE_KEYWORDS),
        "needs_report": _has_any(normalized, _REPORT_KEYWORDS),
        "report_format": "markdown",
    }


class RestrictedSingleAgentRunner:
    """A deterministic, bounded single-agent diagnosis pipeline."""

    def __init__(
        self,
        *,
        message: str,
        thread_id: str,
        user_identity: str,
        request_id: str,
        stream_id: str,
        trace_id: str,
        limits: SingleAgentLimits | None = None,
        model: Any | None = None,
    ) -> None:
        self.message = message
        self.thread_id = thread_id
        self.user_identity = user_identity
        self.request_id = request_id
        self.stream_id = (stream_id or "").strip()
        self.trace_id = trace_id
        self.limits = limits or SingleAgentLimits()
        self.trace = AgentTrace(
            trace_id=trace_id,
            request_id=request_id,
            thread_id=thread_id,
            user_identity=user_identity,
            user_message=message,
            limits=self.limits,
        )
        self.model = model
        self.cancel_handle: Any = None
        self._round_count = 0
        self._tool_call_count = 0
        self._last_step_result: Any = None

    def _resolve_model(self):
        if self.model is not None:
            return self.model
        from langchain_openai import ChatOpenAI

        self.model = ChatOpenAI(
            model=os.getenv("MODEL_NAME"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.2,
        )
        return self.model

    def _is_cancelled(self) -> bool:
        event = getattr(self.cancel_handle, "cancel_event", None)
        return bool(event is not None and event.is_set())

    def _cancel_reason(self) -> str:
        return getattr(self.cancel_handle, "cancel_reason", None) or "cancelled"

    def _start_stage(self, stage: str, message: str) -> float:
        self._round_count += 1
        if self._round_count > self.limits.max_rounds:
            raise SingleAgentExecutionError(f"超过单 Agent 最大阶段轮次限制：{self.limits.max_rounds}")
        self.trace.add_event("stage", stage=stage, status="started", message=message)
        return time.monotonic()

    def _finish_stage(
        self,
        stage: str,
        started_at: float,
        *,
        status: str = "completed",
        message: str = "",
        error: str | None = None,
    ) -> None:
        self.trace.add_event(
            "stage",
            stage=stage,
            status=status,
            message=message,
            error=error,
            duration_ms=round((time.monotonic() - started_at) * 1000, 1),
        )

    def _record_artifact(self, artifact_type: str, artifact: Any, *, stage: str) -> None:
        payload = artifact.model_dump(exclude_none=True) if hasattr(artifact, "model_dump") else _sanitize_for_json(artifact)
        self.trace.add_event(
            "artifact",
            stage=stage,
            status="created",
            artifact_type=artifact_type,
            artifact=payload if isinstance(payload, dict) else {"value": payload},
        )

    def _build_ping_frame(self, stage: str) -> str:
        payload = {
            "type": "ping",
            "timestamp": datetime.now().isoformat(),
            "trace_id": self.trace_id,
            "stage": stage,
            "message": "单 Agent 正在处理，连接保持中...",
        }
        return encode_sse_event("ping", payload, trace_id=self.trace_id)

    def _build_cancel_complete_frame(self) -> str:
        payload = {
            "type": "chat_complete",
            "thread_id": self.thread_id,
            "trace_id": self.trace_id,
            "cancelled": True,
            "cancel_reason": self._cancel_reason(),
            "final_content": "",
            "trace": self.trace.model_dump(exclude_none=True),
            "todos": [],
            "timestamp": datetime.now().isoformat(),
        }
        return encode_sse_event("complete", payload, trace_id=self.trace_id)

    async def _drive_step(
        self,
        coro: Any,
        *,
        stage: str,
        interval: float = 10.0,
    ) -> AsyncGenerator[str, None]:
        task = asyncio.create_task(coro)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    self._last_step_result = task.result()
                    return
                yield self._build_ping_frame(stage)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

    async def _invoke_text_model(self, prompt: str) -> str:
        response = await self._resolve_model().ainvoke(prompt)
        return str(getattr(response, "content", "") or "")

    async def _invoke_json_model(self, prompt: str) -> dict[str, Any]:
        raw_text = await self._invoke_text_model(prompt)
        try:
            return _loads_json_object(_extract_json_text(raw_text))
        except SingleAgentExecutionError as exc:
            repaired_text = await self._invoke_text_model(_build_json_repair_prompt(raw_text, str(exc)))
            try:
                return _loads_json_object(_extract_json_text(repaired_text))
            except SingleAgentExecutionError as repair_exc:
                raise SingleAgentExecutionError(
                    f"模型 JSON 解析失败：{repair_exc}；原始错误：{exc}；原始响应预览：{_preview(raw_text, 500)}"
                ) from repair_exc

    def _decide_capabilities(
        self,
        payload: dict[str, Any],
        request: DiagnosisRequest,
        *,
        report_from_previous_artifact: bool,
    ) -> SingleAgentDecision:
        normalized = (request.user_message or self.message or "").strip()
        payload_sql = payload.get("needs_sql")
        payload_knowledge = payload.get("needs_knowledge")

        needs_sql = bool(payload_sql) if isinstance(payload_sql, bool) else _has_any(normalized, _SQL_KEYWORDS)
        needs_knowledge = (
            bool(payload_knowledge)
            if isinstance(payload_knowledge, bool)
            else bool(request.fault_code_hint) or _has_any(normalized, _KNOWLEDGE_KEYWORDS)
        )
        needs_report = bool(request.needs_report) or _has_any(normalized, _REPORT_KEYWORDS)

        if report_from_previous_artifact:
            return SingleAgentDecision(
                needs_sql=False,
                needs_knowledge=False,
                needs_report=True,
                report_from_previous_artifact=True,
                reason="识别到基于当前线程已有结果生成报告的请求",
            )

        reason_parts = []
        reason_parts.append("需要 SQL" if needs_sql else "跳过 SQL")
        reason_parts.append("需要知识库" if needs_knowledge else "跳过知识库")
        reason_parts.append("需要报告" if needs_report else "跳过报告")
        return SingleAgentDecision(
            needs_sql=needs_sql,
            needs_knowledge=needs_knowledge,
            needs_report=needs_report,
            report_from_previous_artifact=False,
            reason="；".join(reason_parts),
        )

    async def understand_request(self) -> tuple[DiagnosisRequest, SingleAgentDecision]:
        report_from_previous_artifact = _looks_like_report_handoff(self.message) and get_thread_artifact(self.thread_id) is not None
        if report_from_previous_artifact:
            payload = _fallback_understanding_payload(self.message, self.user_identity)
            payload["needs_report"] = True
        else:
            try:
                payload = await self._invoke_json_model(
                    build_single_agent_understanding_prompt(self.message, self.user_identity)
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("请求理解模型失败，使用规则 fallback", thread_id=self.thread_id, error=str(exc))
                payload = _fallback_understanding_payload(self.message, self.user_identity)

        request = build_request_from_payload(
            self.message,
            self.user_identity,
            payload,
            needs_report=None,
            report_format=str(payload.get("report_format") or "markdown"),
        )
        decision = self._decide_capabilities(
            payload,
            request,
            report_from_previous_artifact=report_from_previous_artifact,
        )
        self.trace.add_event(
            "decision",
            stage="understand",
            status="completed",
            decision=decision.model_dump(),
            message=decision.reason,
        )
        self._record_artifact("request", request, stage="understand")
        return request, decision

    def _start_tool_call(self, *, tool_name: str, tool_input: Any, stage: str) -> tuple[str, float, dict[str, Any]]:
        if tool_name not in self.limits.allowed_tools:
            raise SingleAgentExecutionError(f"工具不在单 Agent 白名单内：{tool_name}")
        self._tool_call_count += 1
        if self._tool_call_count > self.limits.max_tool_calls:
            raise SingleAgentExecutionError(f"超过单 Agent 最大工具调用次数限制：{self.limits.max_tool_calls}")
        run_id = f"{tool_name}-{self._tool_call_count}"
        self.trace.add_event(
            "tool_call",
            stage=stage,
            status="started",
            tool=tool_name,
            run_id=run_id,
            input=_sanitize_for_json(tool_input),
        )
        payload = {
            "type": "tool_start",
            "tool": tool_name,
            "input": _sanitize_for_json(tool_input),
            "stage": stage,
            "current_stage": stage,
            "run_id": run_id,
            "trace_id": self.trace_id,
        }
        return run_id, time.monotonic(), payload

    def _finish_tool_call(
        self,
        *,
        tool_name: str,
        run_id: str,
        started_at: float,
        stage: str,
        output: Any,
    ) -> dict[str, Any]:
        duration_ms = round((time.monotonic() - started_at) * 1000, 1)
        result_preview = _preview(output, limit=400)
        self.trace.add_event(
            "tool_result",
            stage=stage,
            status="completed",
            tool=tool_name,
            run_id=run_id,
            result_preview=result_preview,
            duration_ms=duration_ms,
        )
        payload = {
            "type": "tool_end",
            "tool": tool_name,
            "result": _sanitize_for_json(output),
            "result_preview": result_preview,
            "truncated": len(_stringify(output)) > len(result_preview),
            "stage": stage,
            "current_stage": stage,
            "run_id": run_id,
            "trace_id": self.trace_id,
            "stage_duration_ms": duration_ms,
        }
        serialized = json.dumps(_sanitize_for_json(payload), ensure_ascii=False, default=str)
        if len(serialized) > 6000:
            payload.pop("result", None)
            payload["result_preview"] = result_preview[:400] + "...(truncated)"
            payload["truncated"] = True
        return payload

    async def _invoke_restricted_tool(
        self,
        *,
        tool_name: str,
        tool: Any,
        tool_input: Any,
        stage: str,
    ) -> AsyncGenerator[str, None]:
        run_id, started_at, start_payload = self._start_tool_call(
            tool_name=tool_name,
            tool_input=tool_input,
            stage=stage,
        )
        yield encode_sse_event("tool_start", start_payload, trace_id=self.trace_id)
        result = await invoke_tool(tool, tool_input)
        self._last_step_result = result
        end_payload = self._finish_tool_call(
            tool_name=tool_name,
            run_id=run_id,
            started_at=started_at,
            stage=stage,
            output=result,
        )
        yield encode_sse_event("tool_end", end_payload, trace_id=self.trace_id)

    async def stream_sql_step(self, request: DiagnosisRequest) -> AsyncGenerator[str, None]:
        prompt = self._build_sql_prompt(request)
        sql_query, summary = await build_sql_plan(
            prompt,
            self._invoke_json_model,
            default_summary="已生成 SQL 查询",
        )
        if not sql_query or not _is_readonly_sql(sql_query) or _has_unknown_sql_table(sql_query):
            sql_query = _build_fallback_sql_query(request)
            summary = "已使用受限 fallback 查询最近设备故障与关键指标数据"

        tools_map = build_sql_tools_map()
        checker_tool = find_sql_tool(tools_map, "sql_db_query_checker", False)
        if checker_tool is not None:
            async for chunk in self._invoke_restricted_tool(
                tool_name="sql_db_query_checker",
                tool=checker_tool,
                tool_input={"query": sql_query},
                stage="sql",
            ):
                yield chunk
            checked_query_text = _stringify(self._last_step_result).strip()
            if checked_query_text and _is_readonly_sql(checked_query_text) and not _has_unknown_sql_table(checked_query_text):
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
            result_preview=_preview(raw_output),
            raw_output=_stringify(raw_output),
        )
        self._record_artifact("sql", artifact, stage="sql")
        self._last_step_result = artifact

    def _build_sql_prompt(self, request: DiagnosisRequest) -> str:
        return f"""
你是 DCMA 单 Agent 的 SQL 查询规划器。
请输出 JSON：
- sql_query: 单条只读 SELECT SQL
- summary: 一句话说明查询目标

要求：
1. 只输出 JSON。
2. 只允许使用下列可用表结构，不得访问其他表。
3. 优先围绕用户给出的设备、故障码、指标和时间范围查询最近数据。
4. SQL 必须限制返回行数，默认 LIMIT 50。

用户问题：{request.user_message}
分析目标：{request.analysis_goal}
设备提示：{request.equipment_hint}
指标提示：{request.metric_hint}
故障码提示：{request.fault_code_hint}
时间范围提示：{request.time_range_hint}

可用表结构：
{_SQL_SCHEMA_CONTEXT}
""".strip()

    async def stream_knowledge_step(self, request: DiagnosisRequest, sql_artifact: SqlStepArtifact | None) -> AsyncGenerator[str, None]:
        query = build_default_knowledge_query(
            request,
            sql_artifact.summary if sql_artifact and sql_artifact.success else "",
        )
        async for chunk in self._invoke_restricted_tool(
            tool_name="query_knowledge_base",
            tool=get_knowledge_tool(),
            tool_input={"query": query},
            stage="knowledge",
        ):
            yield chunk
        raw_output = _stringify(self._last_step_result)
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
        payload = await self._invoke_json_model(
            build_single_agent_analysis_prompt(
                request,
                sql_artifact.summary,
                sql_artifact.result_preview or sql_artifact.raw_output,
                knowledge_artifact.raw_output,
                current_time,
            )
        )
        artifact = AnalysisStepArtifact(
            success=True,
            conclusion=str(payload.get("conclusion") or "").strip(),
            basis=[str(item).strip() for item in (payload.get("basis") or []) if str(item).strip()],
            recommendations=[str(item).strip() for item in (payload.get("recommendations") or []) if str(item).strip()],
            risk_notice=(str(payload.get("risk_notice")).strip() if payload.get("risk_notice") else None),
            missing_information=[str(item).strip() for item in (payload.get("missing_information") or []) if str(item).strip()],
            confidence=str(payload.get("confidence") or "low").strip().lower() or "low",
            error=None,
        )
        if not artifact.conclusion:
            raise SingleAgentExecutionError("分析阶段未生成结论")
        self._record_artifact("analysis", artifact, stage="analysis")
        return artifact

    async def stream_report_step(
        self,
        request: DiagnosisRequest,
        sql_artifact: SqlStepArtifact,
        knowledge_artifact: KnowledgeStepArtifact,
        analysis_artifact: AnalysisStepArtifact,
        current_time: str,
    ) -> AsyncGenerator[str, None]:
        report_filename = f"dcma_single_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.thread_id[-6:]}"
        payload = {
            "title": "DCMA 故障诊断报告",
            "report_time": current_time,
            "diagnosis_object": request.equipment_hint or "DCMA 系统",
            "diagnosis_type": request.fault_code_hint or "故障诊断",
            "executive_summary": analysis_artifact.conclusion,
            "diagnosis_overview": "本报告由限制型单 Agent 生成，流程包含请求理解、受限 SQL、知识检索、诊断分析和报告保存。",
            "diagnosis_details": (
                f"【SQL 结果摘要】\n{sql_artifact.result_preview or sql_artifact.raw_output or '无'}\n\n"
                f"【知识检索摘要】\n{knowledge_artifact.raw_output or '无'}"
            ),
            "fault_inference": analysis_artifact.conclusion,
            "repair_recommendations": "\n".join(f"- {item}" for item in analysis_artifact.recommendations)
            or "- 暂无具体处置建议",
            "preventive_maintenance": "建议结合本次诊断结果持续跟踪关键指标，并复核相关部件状态。",
            "diagnosis_basis": (
                f"SQL 摘要：{sql_artifact.summary}\n"
                f"SQL 语句：{'; '.join(sql_artifact.sql_used) or '无'}\n"
                f"知识查询：{knowledge_artifact.query or '无'}\n"
                f"分析依据：{'; '.join(analysis_artifact.basis) or '无'}"
            ),
            "report_filename": report_filename,
        }
        async for chunk in self._invoke_restricted_tool(
            tool_name="save_report",
            tool=get_report_tool(),
            tool_input=payload,
            stage="report",
        ):
            yield chunk
        save_result = _stringify(self._last_step_result)
        artifact = ReportStepArtifact(
            success="失败" not in save_result,
            report_filename=_extract_report_filename(save_result, f"{report_filename}.md"),
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
        save_result = _stringify(self._last_step_result)
        artifact = ReportStepArtifact(
            success="失败" not in save_result,
            report_filename=_extract_report_filename(save_result, report_payload.get("report_filename")),
            save_result=save_result,
            error=None if "失败" not in save_result else save_result,
        )
        self._record_artifact("report", artifact, stage="report")
        source_name = "故障诊断结果" if str(envelope.workflow_type) == WorkflowType.FAULT_DIAGNOSIS.value else "结构化结果"
        final_answer = (
            f"已基于当前线程最近一次{source_name}生成报告。\n"
            f"【来源摘要】{envelope.request_summary}\n"
            f"【报告文件】{_extract_report_url(save_result) or artifact.report_filename or '未生成'}\n"
            f"【保存结果】{save_result}"
        )
        self._last_step_result = final_answer, artifact

    async def build_final_answer(
        self,
        analysis_artifact: AnalysisStepArtifact,
        report_artifact: ReportStepArtifact,
    ) -> str:
        report_name = report_artifact.report_filename if report_artifact and report_artifact.report_filename else "未生成"
        prompt = f"""
你是 DCMA 限制型单 Agent 的最终答复整理器。
请用中文生成最终用户答复，结构清晰，必须包含：
1. 一句话结论
2. 数据支撑
3. 处理建议
4. 风险提示或不确定性
5. 报告文件名

结论：{analysis_artifact.conclusion}
依据：{analysis_artifact.basis}
建议：{analysis_artifact.recommendations}
风险提示：{analysis_artifact.risk_notice}
缺失信息：{analysis_artifact.missing_information}
置信度：{analysis_artifact.confidence}
报告文件名：{report_name}
""".strip()
        try:
            final_answer = (await self._invoke_text_model(prompt)).strip()
            if final_answer:
                return final_answer
        except Exception as exc:  # noqa: BLE001
            _log.warning("最终答复整理失败，回退到模板输出", thread_id=self.thread_id, error=str(exc))

        basis_lines = "\n".join(f"- {item}" for item in analysis_artifact.basis) or "- 暂无明确数据支撑"
        recommendation_lines = "\n".join(f"- {item}" for item in analysis_artifact.recommendations) or "- 暂无具体处置建议"
        risk_notice = analysis_artifact.risk_notice or "当前未发现额外风险提示。"
        return (
            f"【结论】{analysis_artifact.conclusion}\n"
            f"【数据支撑】\n{basis_lines}\n"
            f"【处置建议】\n{recommendation_lines}\n"
            f"【风险提示】{risk_notice}\n"
            f"【报告文件】{report_name}"
        )

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
    ) -> WorkflowArtifactEnvelope:
        self.trace.add_event(
            "artifact",
            stage="final_answer",
            status="created",
            artifact_type="workflow_artifact",
            artifact={"workflow_type": WorkflowType.FAULT_DIAGNOSIS.value, "thread_id": self.thread_id},
        )
        evidence = [
            EvidenceItem(
                source_type="sql",
                title="SQL 查询摘要",
                content=sql_artifact.result_preview or sql_artifact.raw_output or sql_artifact.summary,
                importance="high" if sql_artifact.success else "low",
            ),
            EvidenceItem(
                source_type="knowledge_base",
                title="知识检索摘要",
                content=knowledge_artifact.raw_output or knowledge_artifact.error or "未执行知识检索",
                importance="medium" if knowledge_artifact.success else "low",
            ),
            EvidenceItem(
                source_type="analysis",
                title="诊断结论",
                content=analysis_artifact.conclusion,
                importance="high",
            ),
        ]
        envelope = WorkflowArtifactEnvelope(
            workflow_type=WorkflowType.FAULT_DIAGNOSIS,
            thread_id=self.thread_id,
            created_at=datetime.now().isoformat(),
            request_summary=request.analysis_goal or request.user_message,
            final_answer=final_answer,
            report_filename=report_artifact.report_filename,
            payload={
                "runtime": "restricted_single_agent",
                "request": request.model_dump(exclude_none=True),
                "decision": decision.model_dump(),
                "sql_artifact": sql_artifact.model_dump(exclude_none=True),
                "knowledge_artifact": knowledge_artifact.model_dump(exclude_none=True),
                "analysis_artifact": analysis_artifact.model_dump(exclude_none=True),
                "report_artifact": report_artifact.model_dump(exclude_none=True),
                "trace": self.trace.model_dump(exclude_none=True),
            },
            evidence=evidence,
        )
        return save_thread_artifact(envelope)

    def _build_error_payload(self, exc: Exception) -> dict[str, Any]:
        model_gateway_error = classify_model_gateway_error(exc)
        if model_gateway_error:
            category, message = model_gateway_error
            return build_server_error_payload(
                message=message,
                error_id=self.request_id,
                trace_id=self.trace_id,
                code=model_error_code(category),
                retryable=False,
                details={"category": category, "runtime": "restricted_single_agent"},
            )
        return build_server_error_payload(
            message="请求处理失败，请稍后重试",
            error_id=self.request_id,
            trace_id=self.trace_id,
            code="INTERNAL_ERROR",
            retryable=False,
            details={"category": "single_agent", "runtime": "restricted_single_agent", "error": str(exc)},
        )

    async def stream_events(
        self,
        app: "FastAPI",
        *,
        cancel_handle: Any = None,
    ) -> AsyncGenerator[str, None]:
        self.cancel_handle = cancel_handle
        if getattr(app.state, "chat_model", None) is not None and self.model is None:
            self.model = app.state.chat_model

        event_count = 0
        token_count = 0
        started_at = time.monotonic()

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
                        "report_url": _extract_report_url(report_artifact.save_result),
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

            if final_answer.strip():
                yield encode_sse_event("token", {"type": "token", "content": final_answer}, trace_id=self.trace_id)
                token_count += 1
                event_count += 1

            yield encode_sse_event(
                "complete",
                {
                    "type": "chat_complete",
                    "thread_id": self.thread_id,
                    "trace_id": self.trace_id,
                    "runtime": "restricted_single_agent",
                    "final_content": final_answer,
                    "report_filename": report_artifact.report_filename,
                    "report_url": _extract_report_url(report_artifact.save_result),
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
                },
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
            _log.exception(
                "限制型单 Agent 流式请求失败",
                thread_id=self.thread_id,
                stream_id=self.stream_id,
                error=str(exc),
            )
            yield encode_sse_event("server_error", self._build_error_payload(exc), trace_id=self.trace_id)
