"""Real report runtime node for Agent Engine V2."""

from __future__ import annotations

import re
import json
from typing import Any

from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    ComparisonArtifactPayload,
    KnowledgeArtifactPayload,
    ReportArtifactPayload,
    SqlArtifactPayload,
)
from fault_diagnosis.domain.diagnosis.contracts import AnalysisStepArtifact, KnowledgeStepArtifact, ReportStepArtifact, SqlStepArtifact
from ...output.report import build_reportable_payload
from fault_diagnosis.domain.security.runtime_context import reset_current_auth_context, set_current_auth_context
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from ..tool_runtime import ToolRuntime
from .base import auth_context, build_request, input_value, model_to_dict, timestamp_for_filename
from ...artifacts import ArtifactPayloadError, hydrate_artifact_lineage, require_payload, source_envelopes

_REPORT_URL_RE = re.compile(r"(/reports/[A-Za-z0-9_.-]+)")


class ReportNode:
    node_type = "report"

    def __init__(self, tool_runtime: ToolRuntime) -> None:
        self.tool_runtime = tool_runtime

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        artifact_id = str(input_value(node, "artifact_id", "") or node.get("artifact_id") or "")
        report_filename = str(
            input_value(node, "report_filename", "")
            or f"v2_runtime_report_{state.thread_id or state.trace_id or timestamp_for_filename()}"
        )
        chart_payload = input_value(node, "chart_payload", None)
        operation_report_payload = str(input_value(node, "operation_report_payload", "") or "")
        if operation_report_payload == "__runtime_artifacts__":
            operation_report_payload = ""
        if not operation_report_payload:
            target_id = str(input_value(node, "target_artifact_id", "") or input_value(node, "source_artifact_id", "") or "")
            if target_id and not hydrate_artifact_lineage(state, target_id):
                return _missing_report_source(artifact_id, node, "artifact_payload_invalid")
            sources = source_envelopes("report", node=node, state=state)
            if not sources:
                artifact = ReportStepArtifact(
                    artifact_id=artifact_id,
                    success=False,
                    report_title=str(input_value(node, "title", "") or "DCMA 运行诊断报告"),
                    error="missing_reportable_artifact",
                )
                return NodeExecutionOutput(
                    status="failed",
                    output={"success": False, "artifact": model_to_dict(artifact), "error": artifact.error},
                    error={
                        "code": "missing_reportable_artifact",
                        "message": "Report generation requires a referenced artifact or current structured diagnosis payload.",
                    },
                )
            try:
                operation_report_payload = _build_operation_payload(state, node, sources)
            except ArtifactPayloadError as exc:
                return _missing_report_source(artifact_id, node, exc.code)
            except ValueError as exc:
                return _missing_report_source(artifact_id, node, str(exc) or "artifact_payload_invalid")

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
            artifact_id=artifact_id,
            success=success,
            report_filename=report_url.rsplit("/", 1)[-1] if report_url else None,
            report_title=str(input_value(node, "title", "") or "DCMA 运行诊断报告"),
            report_url=report_url,
            save_result=text,
            error=None if success else text or "report_save_failed",
        )
        return NodeExecutionOutput(
            status="completed" if success else "failed",
            output={"success": success, "artifact": model_to_dict(artifact), "report_url": report_url},
            tool_call_refs=["save_report"],
            artifact_payload=ReportArtifactPayload(report_artifact=artifact),
            error=None if success else {"code": "report_save_failed", "message": artifact.error or "Report save failed."},
        )


def _build_operation_payload(state: RuntimeState, node: dict[str, Any], sources: list[Any]) -> str:
    request = build_request(state, node, goal="DCMA 运行诊断报告")
    comparison_sources = [item for item in sources if item.artifact_type == "comparison_artifact"]
    if comparison_sources:
        if len(comparison_sources) != 1:
            raise ValueError("artifact_source_ambiguous")
        comparison = require_payload(comparison_sources[0], ComparisonArtifactPayload).comparison_artifact
        return json.dumps(
            {
                "title": str(input_value(node, "title", "") or "DCMA 运行比较报告"),
                "diagnosis_type": "运行比较",
                "conclusion": comparison.conclusion,
                "devices": comparison.devices,
                "comparison_dimensions": [item.model_dump(mode="json") for item in comparison.comparison_dimensions],
            },
            ensure_ascii=False,
        )
    sql_sources = [item for item in sources if item.artifact_type == "sql_artifact"]
    if len(sql_sources) != 1:
        raise ValueError("artifact_payload_invalid" if not sql_sources else "artifact_source_ambiguous")
    sql_payload = require_payload(sql_sources[0], SqlArtifactPayload)
    knowledge_sources = [item for item in sources if item.artifact_type == "knowledge_artifact"]
    knowledge_artifact = (
        require_payload(knowledge_sources[0], KnowledgeArtifactPayload).knowledge_artifact
        if len(knowledge_sources) == 1
        else KnowledgeStepArtifact(success=False, query="", error="missing_knowledge_artifact")
    )
    analysis_sources = [item for item in sources if item.artifact_type == "analysis_artifact"]
    analysis_artifact = (
        require_payload(analysis_sources[0], AnalysisArtifactPayload).structured_analysis.analysis_artifact
        if len(analysis_sources) == 1
        else AnalysisStepArtifact(success=False, conclusion="缺少分析产物", error="missing_analysis_artifact")
    )
    payload = build_reportable_payload(
        request=request,
        sql_artifact=sql_payload.sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        normalized_rows=sql_payload.normalized_rows,
        title=str(input_value(node, "title", "") or "DCMA 运行诊断报告"),
        diagnosis_type=str(input_value(node, "diagnosis_type", "") or "运行诊断"),
        workorder_suggestion=None,
    )
    return str(payload["operation_report_payload"])


def _missing_report_source(artifact_id: str, node: dict[str, Any], code: str) -> NodeExecutionOutput:
    artifact = ReportStepArtifact(
        artifact_id=artifact_id,
        success=False,
        report_title=str(input_value(node, "title", "") or "DCMA 运行诊断报告"),
        error=code,
    )
    return NodeExecutionOutput(
        status="blocked",
        output={"success": False, "artifact": model_to_dict(artifact), "error": code},
        error={"code": code, "message": "Report requires a complete typed source artifact."},
    )


def _report_url(text: str) -> str | None:
    match = _REPORT_URL_RE.search(text)
    return match.group(1) if match else None
