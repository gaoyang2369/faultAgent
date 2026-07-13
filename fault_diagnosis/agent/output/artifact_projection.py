"""Projection from V2 runtime output to persisted diagnosis artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType, EvidenceBundle
from fault_diagnosis.domain.context.case_store import build_case_state_snapshot
from ..contracts import ArtifactEnvelope, NodeResult, OutputFrame
from .artifact_manifest import build_artifact_manifests, latest_focus_from_manifests


def project_artifact_envelope(
    *,
    thread_id: str,
    output_frame: OutputFrame,
    evidence_bundle: EvidenceBundle | dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    node_results: list[NodeResult] | list[dict[str, Any]] | None = None,
    trace: dict[str, Any] | None = None,
    request_summary: str = "",
    auth_summary: dict[str, Any] | None = None,
) -> DiagnosisArtifactEnvelope:
    """Build the legacy persisted artifact envelope from V2 output."""

    artifact_map = dict(artifacts or {})
    bundle = _dump(evidence_bundle)
    report_artifact = _dump(artifact_map.get("report_artifact")) or {}
    manifests = build_artifact_manifests(
        thread_id=thread_id,
        artifacts=artifact_map,
        evidence_bundle=evidence_bundle,
        node_results=node_results or [],
        trace=trace,
        auth_summary=auth_summary,
    )
    payload = {
        "engine": "agent_engine_v2",
        "output_frame": output_frame.model_dump(mode="json", exclude_none=True),
        "evidence_bundle": bundle,
        "node_results": [_dump(item) for item in node_results or []],
        "trace": dict(trace or {}),
        "artifact_manifests": [item.model_dump(mode="json", exclude_none=True) for item in manifests],
        "artifact_envelopes": [
            _dump(value)
            for value in (artifact_map.get("artifact_envelopes") or {}).values()
        ] if isinstance(artifact_map.get("artifact_envelopes"), dict) else [],
        "latest_focus": latest_focus_from_manifests(manifests),
        "artifacts_by_id": _artifacts_by_id(manifests, artifact_map),
        **{key: _dump(value) for key, value in artifact_map.items() if key != "artifact_envelopes"},
    }
    envelope = DiagnosisArtifactEnvelope(
        workflow_type=_artifact_type(output_frame.answer_variant),
        thread_id=thread_id,
        created_at=datetime.now().isoformat(),
        request_summary=request_summary or output_frame.status_brief or output_frame.answer_variant,
        final_answer=output_frame.final_answer,
        report_filename=report_artifact.get("report_filename") or report_artifact.get("report_url"),
        payload=payload,
        evidence=_evidence_items(evidence_bundle),
    )
    envelope.payload["case_state_snapshot"] = build_case_state_snapshot(envelope)
    return envelope


def _artifact_type(variant: str) -> DiagnosisArtifactType:
    if variant == "clarification":
        return DiagnosisArtifactType.CLARIFICATION
    if variant in {"status_brief", "status_brief_v2", "status_incomplete"}:
        return DiagnosisArtifactType.STATUS_QUERY
    if variant == "report_ready":
        return DiagnosisArtifactType.REPORT_GENERATION
    if variant == "knowledge_answer":
        return DiagnosisArtifactType.KNOWLEDGE_QA
    return DiagnosisArtifactType.FAULT_DIAGNOSIS


def _evidence_items(evidence_bundle: EvidenceBundle | dict[str, Any] | None) -> list[Any]:
    if isinstance(evidence_bundle, EvidenceBundle):
        return list(evidence_bundle.evidence_items)
    if isinstance(evidence_bundle, dict):
        bundle = EvidenceBundle.model_validate(evidence_bundle)
        return list(bundle.evidence_items)
    return []


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return value


def _artifacts_by_id(manifests: list[Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    canonical = artifacts.get("artifact_envelopes")
    if isinstance(canonical, dict):
        result: dict[str, Any] = {}
        for artifact_id, raw in canonical.items():
            try:
                envelope = raw if isinstance(raw, ArtifactEnvelope) else ArtifactEnvelope.model_validate(raw)
            except Exception:
                continue
            result[str(artifact_id)] = _dump(envelope.payload)
        if result:
            return result
    key_by_type = {
        "knowledge_artifact": "knowledge_artifact",
        "analysis_artifact": "analysis_artifact",
        "comparison_artifact": "comparison_artifact",
        "report_artifact": "report_artifact",
        "workorder_artifact": "workorder_draft",
    }
    result: dict[str, Any] = {}
    sql_registry = artifacts.get("sql_artifacts") if isinstance(artifacts.get("sql_artifacts"), dict) else {}
    for manifest in manifests:
        if manifest.artifact_type == "sql_artifact" and manifest.artifact_id in sql_registry:
            result[manifest.artifact_id] = _dump(sql_registry[manifest.artifact_id])
            continue
        key = key_by_type.get(manifest.artifact_type)
        if key and artifacts.get(key) is not None:
            result[manifest.artifact_id] = _dump(artifacts[key])
    return result
