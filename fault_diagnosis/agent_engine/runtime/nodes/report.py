"""Real report runtime node for Agent Engine V2."""

from __future__ import annotations

import re
from typing import Any

from ....diagnosis.contracts import AnalysisStepArtifact, KnowledgeStepArtifact, ReportStepArtifact, SqlStepArtifact
from ...output.report import build_reportable_payload
from ....security.runtime_context import reset_current_auth_context, set_current_auth_context
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from ..tool_runtime import ToolRuntime
from .base import auth_context, build_request, input_value, model_to_dict, timestamp_for_filename

_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9_.-]+)")


class ReportNode:
    node_type = "report"

    def __init__(self, tool_runtime: ToolRuntime) -> None:
        self.tool_runtime = tool_runtime

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        report_filename = str(
            input_value(node, "report_filename", "")
            or f"v2_runtime_report_{state.thread_id or state.trace_id or timestamp_for_filename()}"
        )
        chart_payload = input_value(node, "chart_payload", None)
        operation_report_payload = str(input_value(node, "operation_report_payload", "") or "")
        if not operation_report_payload:
            operation_report_payload = _build_operation_payload(state, node)

        auth = auth_context(state)
        token = set_current_auth_context(auth)
        try:
            save_result = self.tool_runtime.save_report(
                report_filename=report_filename,
                chart_payload=chart_payload,
                operation_report_payload=operation_report_payload,
            )
        finally:
            reset_current_auth_context(token)

        text = str(save_result or "")
        report_url = _report_url(text)
        success = bool(report_url) and "报告保存失败" not in text
        artifact = ReportStepArtifact(
            success=success,
            report_filename=report_url.rsplit("/", 1)[-1] if report_url else None,
            report_title=str(input_value(node, "title", "") or "DCMA 运行诊断报告"),
            report_url=report_url,
            save_result=text,
            error=None if success else text or "report_save_failed",
        )
        state.artifacts["report_artifact"] = artifact
        return NodeExecutionOutput(
            status="completed" if success else "failed",
            output={"success": success, "artifact": model_to_dict(artifact), "report_url": report_url},
            tool_call_refs=["save_report"],
            artifacts={"report_artifact": artifact},
            error=None if success else {"code": "report_save_failed", "message": artifact.error or "Report save failed."},
        )


def _build_operation_payload(state: RuntimeState, node: dict[str, Any]) -> str:
    request = build_request(state, node, goal="DCMA 运行诊断报告")
    sql_artifact = _model(state.artifacts.get("sql_artifact"), SqlStepArtifact, SqlStepArtifact(success=False, summary="无 SQL 数据"))
    knowledge_artifact = _model(
        state.artifacts.get("knowledge_artifact"),
        KnowledgeStepArtifact,
        KnowledgeStepArtifact(success=False, query="", error="missing_knowledge_artifact"),
    )
    analysis_artifact = _model(
        state.artifacts.get("analysis_artifact"),
        AnalysisStepArtifact,
        AnalysisStepArtifact(success=False, conclusion="缺少分析产物", error="missing_analysis_artifact"),
    )
    payload = build_reportable_payload(
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        normalized_rows=state.artifacts.get("sql_rows") if isinstance(state.artifacts.get("sql_rows"), list) else None,
        title=str(input_value(node, "title", "") or "DCMA 运行诊断报告"),
        diagnosis_type=str(input_value(node, "diagnosis_type", "") or "运行诊断"),
        workorder_suggestion=state.artifacts.get("workorder_suggestion"),
    )
    return str(payload["operation_report_payload"])


def _model(value: Any, model_type: Any, default: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type.model_validate(value)
    return default


def _report_url(text: str) -> str | None:
    match = _REPORT_URL_RE.search(text)
    return match.group(1) if match else None
