"""Canonical, storage-neutral ArtifactEnvelope contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ArtifactType = Literal[
    "knowledge_artifact",
    "sql_artifact",
    "analysis_artifact",
    "structured_analysis_artifact",
    "report_artifact",
    "workorder_artifact",
    "comparison_artifact",
]


class _ArtifactContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ArtifactLineage(_ArtifactContract):
    schema_version: str = "artifact_lineage.v1"
    lineage_status: Literal["complete", "legacy_partial", "invalid"] = "legacy_partial"
    artifact_id: str = ""
    artifact_type: str = ""
    subject_device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    source_evidence_bundle_ids: list[str] = Field(default_factory=list)
    data_basis: list[dict[str, Any]] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    time_windows: list[dict[str, Any]] = Field(default_factory=list)
    created_from_goal_ids: list[str] = Field(default_factory=list)


class ArtifactManifest(_ArtifactContract):
    schema_version: str = "artifact_manifest.v1"
    artifact_id: str = ""
    artifact_type: ArtifactType
    thread_id: str = ""
    turn_id: str = ""
    request_id: str = ""
    trace_id: str = ""
    produced_by_skill: str = ""
    produced_by_nodes: list[str] = Field(default_factory=list)
    status: Literal["completed", "failed", "partial"] = "completed"
    artifact_status: Literal["complete", "legacy_partial", "invalid", "failed"] = "legacy_partial"
    persistence_status: Literal["staged", "committed", "failed", "not_persisted"] = "not_persisted"
    readback_verified: bool = False
    followupable: bool = False
    reportable: bool = False
    actionable: bool = False
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    time_window: dict[str, Any] = Field(default_factory=dict)
    source_table: str = ""
    data_window: dict[str, Any] = Field(default_factory=dict)
    requested_window: dict[str, Any] = Field(default_factory=dict)
    resolved_window: dict[str, Any] = Field(default_factory=dict)
    data_basis: dict[str, Any] = Field(default_factory=dict)
    latest_sample_time: str = ""
    sample_count: int = 0
    runtime_status: str = "unknown"
    key_findings: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    freshness: str = "unknown"
    currentness: str = ""
    severity: str = ""
    risk_level: str = ""
    status_level: str = ""
    diagnosis_summary: str = ""
    findings: list[str] = Field(default_factory=list)
    probable_causes: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_bundle_id: str = ""
    report_url: str = ""
    report_filename: str = ""
    linked_analysis_artifact_id: str = ""
    linked_sql_artifact_id: str = ""
    linked_evidence_bundle_id: str = ""
    source_file: str = ""
    source_page: str = ""
    parsed_manual_fields: dict[str, Any] = Field(default_factory=dict)
    available_followups: list[str] = Field(default_factory=list)
    supported_followup_capabilities: list[str] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    authorization_scope_summary: dict[str, Any] = Field(default_factory=dict)
    draft_only: bool = False
    manual_confirmation_required: bool = False
    dispatch_forbidden: bool = False
    owner_user_id: str = ""
    owner_session_id: str = ""
    lineage: ArtifactLineage = Field(default_factory=ArtifactLineage)


class ArtifactEnvelope(_ArtifactContract):
    schema_version: str = "artifact_envelope.v2"
    artifact_id: str
    artifact_type: ArtifactType
    owner_user_id: str = ""
    owner_session_id: str = ""
    thread_id: str
    request_id: str = ""
    trace_id: str = ""
    turn_id: str = ""
    produced_by_node: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    manifest: ArtifactManifest
    lineage: ArtifactLineage
    status: Literal["complete", "legacy_partial", "invalid", "failed"] = "complete"
    persistence_status: Literal["staged", "committed", "failed", "not_persisted"] = "not_persisted"
    readback_verified: bool = False

    @model_validator(mode="after")
    def validate_identity(self) -> "ArtifactEnvelope":
        if self.manifest.artifact_id != self.artifact_id:
            raise ValueError("manifest.artifact_id must equal envelope.artifact_id")
        if self.lineage.artifact_id != self.artifact_id:
            raise ValueError("lineage.artifact_id must equal envelope.artifact_id")
        if self.manifest.artifact_type != self.artifact_type:
            raise ValueError("manifest.artifact_type must equal envelope.artifact_type")
        if self.lineage.artifact_type != self.artifact_type:
            raise ValueError("lineage.artifact_type must equal envelope.artifact_type")
        return self
