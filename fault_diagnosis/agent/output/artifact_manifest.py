"""Project canonical ArtifactEnvelope manifests at the compatibility boundary."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.agent.contracts import ArtifactEnvelope, ArtifactManifest
from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle


def build_artifact_manifests(
    *,
    thread_id: str,  # noqa: ARG001 - retained for boundary compatibility
    artifacts: dict[str, Any] | None = None,
    evidence_bundle: EvidenceBundle | dict[str, Any] | None = None,  # noqa: ARG001
    node_results: list[Any] | None = None,  # noqa: ARG001
    trace: dict[str, Any] | None = None,  # noqa: ARG001
    request_id: str = "",  # noqa: ARG001
    auth_summary: dict[str, Any] | None = None,  # noqa: ARG001
) -> list[ArtifactManifest]:
    """Return propagated manifests; never manufacture identity or lineage."""

    raw = (artifacts or {}).get("artifact_envelopes")
    values = list(raw.values()) if isinstance(raw, dict) else list(raw or []) if isinstance(raw, list) else []
    envelopes: list[ArtifactEnvelope] = []
    for value in values:
        try:
            envelope = value if isinstance(value, ArtifactEnvelope) else ArtifactEnvelope.model_validate(value)
        except Exception:
            continue
        if envelope.status == "complete":
            envelopes.append(envelope)
    return [item.manifest.model_copy(deep=True) for item in envelopes]


def latest_focus_from_manifests(manifests: list[ArtifactManifest]) -> dict[str, Any]:
    """Project a legacy latest-focus shape from already canonical manifests."""

    selected = select_focus_manifest(manifests)
    if selected is None:
        return {}
    return {
        "artifact_id": selected.artifact_id,
        "artifact_type": selected.artifact_type,
        "device_refs": list(selected.device_refs),
        "fault_code_refs": list(selected.fault_code_refs),
        "evidence_bundle_id": selected.evidence_bundle_id or selected.linked_evidence_bundle_id,
        "report_id": selected.report_url or selected.report_filename,
        "freshness": selected.freshness,
        "severity": selected.severity,
        "diagnosis_summary": selected.diagnosis_summary,
        "available_followups": list(selected.available_followups),
        "supported_followup_capabilities": list(selected.supported_followup_capabilities),
        "available_actions": list(selected.available_actions),
    }


def select_focus_manifest(manifests: list[ArtifactManifest]) -> ArtifactManifest | None:
    """Compatibility-only display selection; never creates or mutates identity."""

    priority = {
        "workorder_artifact": 0,
        "report_artifact": 1,
        "analysis_artifact": 2,
        "knowledge_artifact": 3,
        "comparison_artifact": 4,
        "sql_artifact": 5,
        "structured_analysis_artifact": 6,
    }
    usable = [
        item
        for item in manifests
        if item.status == "completed" and (item.followupable or item.actionable or item.reportable)
    ]
    return sorted(usable, key=lambda item: priority.get(item.artifact_type, 99))[0] if usable else None
