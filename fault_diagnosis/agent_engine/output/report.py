"""Structured report payload builder for Agent Engine V2."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ...diagnosis.report_mapper import _operation_payload


def build_reportable_payload(
    *,
    request: DiagnosisRequest | dict[str, Any],
    sql_artifact: SqlStepArtifact | dict[str, Any],
    knowledge_artifact: KnowledgeStepArtifact | dict[str, Any],
    analysis_artifact: AnalysisStepArtifact | dict[str, Any],
    workorder_suggestion: WorkOrderSuggestion | dict[str, Any] | None = None,
    normalized_rows: list[dict[str, Any]] | None = None,
    title: str = "DCMA 运行诊断报告",
    diagnosis_type: str = "运行诊断",
    report_time: str | None = None,
    report_filename: str | None = None,
    chart_payload: Any = "",
) -> dict[str, Any]:
    """Build save_report inputs from structured V2 artifacts only."""

    request_model = _model(request, DiagnosisRequest)
    sql_model = _model(sql_artifact, SqlStepArtifact)
    if normalized_rows and not sql_model.raw_output:
        sql_model = sql_model.model_copy(update={"raw_output": json.dumps(normalized_rows, ensure_ascii=False)})
    knowledge_model = _model(knowledge_artifact, KnowledgeStepArtifact)
    analysis_model = _model(analysis_artifact, AnalysisStepArtifact)
    workorder_model = _optional_model(workorder_suggestion, WorkOrderSuggestion)
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


def _model(value: Any, model_type: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type.model_validate(value)
    raise TypeError(f"{model_type.__name__} structured payload is required")


def _optional_model(value: Any, model_type: Any) -> Any:
    if value is None:
        return None
    return _model(value, model_type)
