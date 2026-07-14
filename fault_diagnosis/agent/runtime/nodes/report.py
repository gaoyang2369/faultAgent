"""Real report runtime node for Agent Engine V2."""

from __future__ import annotations

import re
from typing import Any

from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    ReportArtifactPayload,
    ReportInputSnapshot,
    SqlArtifactPayload,
)
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
)
from ...output.report import build_reportable_payload
from fault_diagnosis.domain.security.runtime_context import reset_current_auth_context, set_current_auth_context
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from ..tool_runtime import ToolRuntime
from .base import auth_context, input_value, model_to_dict, timestamp_for_filename
from ...artifacts import ArtifactPayloadError, load_bound_artifacts, require_payload

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
        try:
            report_sources = load_bound_artifacts(node=node, state=state, roles=("report_source",))
            tabular_sources = load_bound_artifacts(node=node, state=state, roles=("tabular_source",))
            if len(report_sources) != 1:
                raise ValueError("report_source_cardinality")
            analysis_payload = require_payload(report_sources[0], AnalysisArtifactPayload)
            snapshot = analysis_payload.report_input_snapshot
            if snapshot is None:
                raise ValueError("legacy_analysis_missing_report_input_snapshot")
            operation_report_payload = _build_operation_payload(
                node=node,
                snapshot=snapshot,
                tabular_sources=tabular_sources,
            )
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
            artifact_payload=ReportArtifactPayload(
                report_artifact=artifact,
                report_input_snapshot=snapshot,
            ),
            error=None if success else {"code": "report_save_failed", "message": artifact.error or "Report save failed."},
        )


def _build_operation_payload(
    *,
    node: dict[str, Any],
    snapshot: ReportInputSnapshot,
    tabular_sources: list[Any],
) -> str:
    if len(tabular_sources) > 1:
        raise ValueError("report_tabular_source_cardinality")
    expected_tabular = snapshot.tabular_source_sql_artifact_id
    actual_tabular = tabular_sources[0].artifact_id if tabular_sources else None
    if actual_tabular != expected_tabular:
        raise ValueError("report_tabular_source_snapshot_mismatch")
    sql_payload = require_payload(tabular_sources[0], SqlArtifactPayload) if tabular_sources else None
    request = DiagnosisRequest(
        user_message=snapshot.diagnosis_summary,
        user_identity="canonical_report_snapshot",
        equipment_hint=snapshot.device_refs[0] if len(snapshot.device_refs) == 1 else None,
        fault_code_hint=snapshot.fault_codes[0] if len(snapshot.fault_codes) == 1 else None,
        needs_report=True,
        report_format="html",
        analysis_goal=snapshot.diagnosis_summary,
    )
    sql_artifact = (
        sql_payload.sql_artifact
        if sql_payload is not None
        else SqlStepArtifact(
            success=True,
            summary="ReportInputSnapshot",
            query_status="success",
            source_table=str(snapshot.runtime_summary.get("source_table") or ""),
            row_count=snapshot.sample_count,
            sample_count=snapshot.sample_count or 0,
            requested_window=dict(snapshot.requested_window or {}),
            resolved_window=dict(snapshot.resolved_window or {}),
            latest_sample_time=snapshot.latest_sample_time or "",
            runtime_status=str(snapshot.runtime_summary.get("runtime_status") or "unknown"),
            status_reasons=[str(item) for item in snapshot.runtime_summary.get("status_reasons", [])],
            key_findings=[str(item) for item in snapshot.runtime_summary.get("key_findings", [])],
            supporting_evidence_ids=list(snapshot.supporting_evidence_ids),
        )
    )
    analysis_artifact = AnalysisStepArtifact(
        success=True,
        conclusion=snapshot.diagnosis_summary,
        basis=[str(item.get("summary") or "") for item in snapshot.structured_findings if item.get("summary")],
        recommendations=list(snapshot.recommendations),
    )
    payload = build_reportable_payload(
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=KnowledgeStepArtifact(success=False, query="", error="not_in_report_snapshot"),
        analysis_artifact=analysis_artifact,
        normalized_rows=sql_payload.normalized_rows if sql_payload is not None else [],
        title=str(input_value(node, "title", "") or "DCMA 运行诊断报告"),
        diagnosis_type=str(input_value(node, "diagnosis_type", "") or "运行诊断"),
        workorder_suggestion=None,
        report_time=snapshot.generated_at,
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
