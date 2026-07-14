"""Strict loader for persisted pre-v3 canonical artifact payloads."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .contracts import (
    AnalysisArtifactPayload,
    ArtifactEnvelope,
    ArtifactPayload,
    ComparisonArtifactPayload,
    KnowledgeArtifactPayload,
    ReportArtifactPayload,
    SqlArtifactPayload,
    WorkorderArtifactPayload,
)


def load_artifact_envelope(value: ArtifactEnvelope | dict[str, Any] | str) -> ArtifactEnvelope | None:
    if isinstance(value, ArtifactEnvelope):
        return value
    try:
        raw = json.loads(value) if isinstance(value, str) else dict(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    try:
        return ArtifactEnvelope.model_validate(raw)
    except ValidationError:
        pass
    payload = upgrade_legacy_payload(str(raw.get("artifact_type") or ""), raw.get("payload"))
    if payload is None:
        return None
    if not legacy_payload_is_reusable(raw.get("manifest") or {}, raw.get("lineage") or {}, payload):
        return None
    normalized = dict(raw)
    normalized["schema_version"] = "artifact_envelope.v3"
    normalized["payload"] = payload
    if normalized.get("artifact_type") == "structured_analysis_artifact":
        normalized["artifact_type"] = "analysis_artifact"
        for key in ("manifest", "lineage"):
            item = dict(normalized.get(key) or {})
            item["artifact_type"] = "analysis_artifact"
            normalized[key] = item
    try:
        return ArtifactEnvelope.model_validate(normalized)
    except ValidationError:
        return None


def load_artifact_envelope_json(value: str) -> ArtifactEnvelope | None:
    return load_artifact_envelope(value)


def upgrade_legacy_payload(artifact_type: str, value: Any) -> ArtifactPayload | None:
    if not isinstance(value, dict):
        return None
    try:
        if artifact_type == "sql_artifact":
            return SqlArtifactPayload(
                sql_artifact=value["sql_artifact"],
                runtime_status_assessment=value["runtime_status_assessment"],
                normalized_rows=value.get("normalized_rows") or [],
            )
        if artifact_type == "knowledge_artifact":
            return KnowledgeArtifactPayload(knowledge_artifact=value["knowledge_artifact"])
        if artifact_type in {"analysis_artifact", "structured_analysis_artifact"}:
            return AnalysisArtifactPayload(
                structured_analysis=value["structured_analysis"],
                report_input_snapshot=value.get("report_input_snapshot"),
            )
        if artifact_type == "comparison_artifact":
            return ComparisonArtifactPayload(comparison_artifact=value["comparison_artifact"])
        if artifact_type == "report_artifact":
            return ReportArtifactPayload(
                report_artifact=value["report_artifact"],
                report_input_snapshot=value.get("report_input_snapshot"),
            )
        if artifact_type == "workorder_artifact":
            return WorkorderArtifactPayload(
                workorder_suggestion=value.get("workorder_suggestion"),
                workorder_draft=value["workorder_draft"],
                pending_action=value["pending_action"],
            )
    except (KeyError, TypeError, ValidationError, ValueError):
        return None
    return None


def legacy_payload_is_reusable(manifest: Any, lineage: Any, payload: ArtifactPayload) -> bool:
    """Require the same structured proof that made a v2 record reusable."""

    manifest_data = manifest.model_dump(mode="python") if hasattr(manifest, "model_dump") else dict(manifest or {})
    lineage_data = lineage.model_dump(mode="python") if hasattr(lineage, "model_dump") else dict(lineage or {})
    devices = [str(item) for item in (lineage_data.get("subject_device_refs") or manifest_data.get("device_refs") or []) if str(item)]
    source_ids = [str(item) for item in (lineage_data.get("source_artifact_ids") or []) if str(item)]
    evidence_refs = [str(item) for item in (manifest_data.get("evidence_refs") or manifest_data.get("supporting_evidence_ids") or []) if str(item)]
    if lineage_data.get("lineage_status") != "complete":
        return False
    if isinstance(payload, SqlArtifactPayload):
        return bool(devices and payload.sql_artifact.source_table and evidence_refs)
    if isinstance(payload, KnowledgeArtifactPayload):
        return bool(payload.knowledge_artifact.fault_code_entries and evidence_refs)
    if isinstance(payload, AnalysisArtifactPayload):
        return bool(devices and source_ids and evidence_refs)
    if isinstance(payload, ComparisonArtifactPayload):
        return bool(len(devices) >= 2 and len(source_ids) >= 2 and payload.comparison_artifact.comparison_dimensions)
    if isinstance(payload, ReportArtifactPayload):
        report = payload.report_artifact
        return bool(devices and source_ids and report.success and (report.report_url or report.report_filename))
    if isinstance(payload, WorkorderArtifactPayload):
        return bool(len(devices) == 1 and source_ids and payload.workorder_draft)
    return False
