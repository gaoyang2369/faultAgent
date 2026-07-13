"""Canonical artifact contracts shared by runtime and persistence."""

from .contracts import (
    AnalysisArtifactPayload,
    ArtifactEnvelope,
    ArtifactLineage,
    ArtifactManifest,
    ArtifactPayload,
    ArtifactPayloadModel,
    ArtifactType,
    ComparisonArtifactPayload,
    KnowledgeArtifactPayload,
    ReportArtifactPayload,
    SqlArtifactPayload,
    WorkorderArtifactPayload,
)
from .legacy_loader import (
    legacy_payload_is_reusable,
    load_artifact_envelope,
    load_artifact_envelope_json,
    upgrade_legacy_payload,
)

__all__ = [
    "AnalysisArtifactPayload",
    "ArtifactEnvelope",
    "ArtifactLineage",
    "ArtifactManifest",
    "ArtifactPayload",
    "ArtifactPayloadModel",
    "ArtifactType",
    "ComparisonArtifactPayload",
    "KnowledgeArtifactPayload",
    "ReportArtifactPayload",
    "SqlArtifactPayload",
    "WorkorderArtifactPayload",
    "load_artifact_envelope",
    "load_artifact_envelope_json",
    "legacy_payload_is_reusable",
    "upgrade_legacy_payload",
]
