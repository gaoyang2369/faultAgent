"""Boundary-only projection from canonical artifacts to the legacy output map."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    ArtifactEnvelope,
    ComparisonArtifactPayload,
    KnowledgeArtifactPayload,
    ReportArtifactPayload,
    SqlArtifactPayload,
    WorkorderArtifactPayload,
)


def project_runtime_artifact_view(
    artifact_registry: dict[str, ArtifactEnvelope],
    node_results: list[Any],
) -> dict[str, Any]:
    """Serialize typed registry values into the unchanged Phase 1/SSE shape."""

    ordered_ids = [str(_value(item, "artifact_id") or "") for item in node_results]
    ordered = [
        artifact_registry[item]
        for item in ordered_ids
        if item in artifact_registry and artifact_registry[item].persistence_status != "failed"
    ]
    if not ordered and not any(ordered_ids):
        ordered = [item for item in artifact_registry.values() if item.status == "complete"]
    produced_registry = {
        artifact_id: artifact_registry[artifact_id]
        for artifact_id in dict.fromkeys(ordered_ids)
        if artifact_id in artifact_registry and artifact_registry[artifact_id].status == "complete"
    }
    view: dict[str, Any] = {"artifact_envelopes": produced_registry}
    sql_ids: list[str] = []
    sql_artifacts: dict[str, Any] = {}
    assessments: dict[str, Any] = {}
    rows_by_device: dict[str, Any] = {}
    for envelope in ordered:
        payload = envelope.payload
        if isinstance(payload, SqlArtifactPayload):
            sql_ids.append(envelope.artifact_id)
            sql_artifacts[envelope.artifact_id] = payload.sql_artifact
            assessments[payload.runtime_status_assessment.device] = payload.runtime_status_assessment
            rows_by_device[payload.runtime_status_assessment.device] = payload.normalized_rows
            view.update(
                {
                    "sql_artifact": payload.sql_artifact,
                    "sql_rows": payload.normalized_rows,
                    "runtime_status_assessment": payload.runtime_status_assessment,
                }
            )
        elif isinstance(payload, KnowledgeArtifactPayload):
            view["knowledge_artifact"] = payload.knowledge_artifact
        elif isinstance(payload, AnalysisArtifactPayload):
            view["analysis_artifact"] = payload.structured_analysis.analysis_artifact
            view["structured_analysis"] = payload.structured_analysis
        elif isinstance(payload, ComparisonArtifactPayload):
            view["comparison_artifact"] = payload.comparison_artifact
        elif isinstance(payload, ReportArtifactPayload):
            view["report_artifact"] = payload.report_artifact
        elif isinstance(payload, WorkorderArtifactPayload):
            if payload.workorder_suggestion is not None:
                view["workorder_suggestion"] = payload.workorder_suggestion
            view["workorder_draft"] = payload.workorder_draft
            view["workorder_pending_action"] = payload.pending_action
    if sql_ids:
        view["sql_artifact_ids"] = list(dict.fromkeys(sql_ids))
        view["sql_artifacts"] = sql_artifacts
        view["runtime_status_assessments"] = assessments
        view["sql_rows_by_device"] = rows_by_device
    for result in node_results:
        if _value(result, "node_type") == "clarification" and _value(result, "status") == "completed":
            view["clarification"] = _value(result, "output") or {}
    return view


def _value(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)
