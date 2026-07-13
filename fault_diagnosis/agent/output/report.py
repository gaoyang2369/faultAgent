"""Structured report payload builder for Agent Engine V2."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from fault_diagnosis.domain.diagnosis.report_mapper import _operation_payload


def build_reportable_payload(
    *,
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion | None = None,
    normalized_rows: list[dict[str, Any]] | None = None,
    title: str = "DCMA 运行诊断报告",
    diagnosis_type: str = "运行诊断",
    report_time: str | None = None,
    report_filename: str | None = None,
    chart_payload: Any = "",
) -> dict[str, Any]:
    """Build save_report inputs from structured V2 artifacts only."""

    required = (
        (request, DiagnosisRequest),
        (sql_artifact, SqlStepArtifact),
        (knowledge_artifact, KnowledgeStepArtifact),
        (analysis_artifact, AnalysisStepArtifact),
    )
    for value, model_type in required:
        if not isinstance(value, model_type):
            raise TypeError(f"{model_type.__name__} structured payload is required")
    if workorder_suggestion is not None and not isinstance(workorder_suggestion, WorkOrderSuggestion):
        raise TypeError("WorkOrderSuggestion structured payload is required")
    request_model = request
    sql_model = sql_artifact
    if normalized_rows and not sql_model.raw_output:
        sql_model = sql_model.model_copy(update={"raw_output": json.dumps(normalized_rows, ensure_ascii=False)})
    knowledge_model = knowledge_artifact
    analysis_model = analysis_artifact
    workorder_model = workorder_suggestion
    payload = _operation_payload(
        title=title,
        diagnosis_type=diagnosis_type,
        report_time=report_time or datetime.now().strftime("%Y年%m月%d日 %H:%M"),
        request=request_model,
        sql_artifact=sql_model,
        knowledge_artifact=knowledge_model,
        analysis_artifact=analysis_model,
        workorder_suggestion=workorder_model,
    )
    result: dict[str, Any] = {
        "title": title,
        "chart_payload": chart_payload,
        "operation_report_payload": payload,
    }
    if report_filename:
        result["report_filename"] = report_filename
    return result
