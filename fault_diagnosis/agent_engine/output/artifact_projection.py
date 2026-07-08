"""Projection from V2 runtime output to persisted diagnosis artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...diagnosis.artifact_store import save_thread_artifact
from ...diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType, EvidenceBundle
from ..contracts import NodeResult, OutputFrame


def project_artifact_envelope(
    *,
    thread_id: str,
    output_frame: OutputFrame,
    evidence_bundle: EvidenceBundle | dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    node_results: list[NodeResult] | list[dict[str, Any]] | None = None,
    trace: dict[str, Any] | None = None,
    request_summary: str = "",
) -> DiagnosisArtifactEnvelope:
    """Build the legacy persisted artifact envelope from V2 output."""

    artifact_map = dict(artifacts or {})
    bundle = _dump(evidence_bundle)
    report_artifact = _dump(artifact_map.get("report_artifact")) or {}
    payload = {
        "engine": "agent_engine_v2",
        "output_frame": output_frame.model_dump(mode="json", exclude_none=True),
        "evidence_bundle": bundle,
        "node_results": [_dump(item) for item in node_results or []],
        "trace": dict(trace or {}),
        **{key: _dump(value) for key, value in artifact_map.items()},
    }
    return DiagnosisArtifactEnvelope(
        workflow_type=_artifact_type(output_frame.answer_variant),
        thread_id=thread_id,
        created_at=datetime.now().isoformat(),
        request_summary=request_summary or output_frame.status_brief or output_frame.answer_variant,
        final_answer=output_frame.final_answer,
        report_filename=report_artifact.get("report_filename") or report_artifact.get("report_url"),
        payload=payload,
        evidence=_evidence_items(evidence_bundle),
    )


def save_v2_artifact(**kwargs: Any) -> DiagnosisArtifactEnvelope:
    """Project and save a V2 artifact envelope through the existing artifact store."""

    return save_thread_artifact(project_artifact_envelope(**kwargs))


def _artifact_type(variant: str) -> DiagnosisArtifactType:
    if variant == "clarification":
        return DiagnosisArtifactType.CLARIFICATION
    if variant == "status_brief":
        return DiagnosisArtifactType.STATUS_QUERY
    if variant == "report_ready":
        return DiagnosisArtifactType.REPORT_GENERATION
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
