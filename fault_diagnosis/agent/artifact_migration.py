"""Explicit offline migration for legacy Analysis Artifacts without report snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from fault_diagnosis.agent.context.artifact_access import resolve_target_artifact
from fault_diagnosis.agent.report_snapshot import build_report_input_snapshot
from fault_diagnosis.domain.artifacts import AnalysisArtifactPayload, ArtifactEnvelope, SqlArtifactPayload
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import commit_artifact


MIGRATION_TOOL_VERSION = "analysis-report-snapshot-migration.v1"


@dataclass(frozen=True)
class AnalysisSnapshotMigrationResult:
    status: str
    source_analysis_artifact_id: str
    source_sql_artifact_id: str
    migrated_artifact_id: str
    write_performed: bool
    envelope: ArtifactEnvelope


def migrate_analysis_report_snapshot(
    *,
    thread_id: str,
    analysis_artifact_id: str,
    sql_artifact_id: str,
    auth_context: AuthContext,
    apply: bool,
    migration_timestamp: str | None = None,
) -> AnalysisSnapshotMigrationResult:
    """Create one new Analysis version from two explicit, authorized Artifact IDs."""

    analysis = _load_explicit(
        thread_id=thread_id,
        artifact_id=analysis_artifact_id,
        expected_type="analysis_artifact",
        auth_context=auth_context,
    )
    sql = _load_explicit(
        thread_id=thread_id,
        artifact_id=sql_artifact_id,
        expected_type="sql_artifact",
        auth_context=auth_context,
    )
    if not isinstance(analysis.payload, AnalysisArtifactPayload):
        raise ValueError("analysis_migration_payload_type_mismatch")
    if not isinstance(sql.payload, SqlArtifactPayload):
        raise ValueError("analysis_migration_sql_payload_type_mismatch")
    declared_sql_id = analysis.manifest.linked_sql_artifact_id
    direct_ids = list(analysis.lineage.source_artifact_ids)
    if declared_sql_id:
        if declared_sql_id != sql_artifact_id:
            raise ValueError("analysis_migration_explicit_sql_mismatch")
    elif direct_ids != [sql_artifact_id]:
        raise ValueError(
            "analysis_migration_sql_source_ambiguous" if len(direct_ids) > 1 else "analysis_migration_explicit_sql_mismatch"
        )

    timestamp = migration_timestamp or datetime.now(UTC).isoformat()
    new_id = _migration_artifact_id(analysis_artifact_id, sql_artifact_id, timestamp)
    snapshot = build_report_input_snapshot(
        structured_analysis=analysis.payload.structured_analysis,
        sql_source=sql,
        generated_at=timestamp,
    )
    payload = analysis.payload.model_copy(update={"report_input_snapshot": snapshot}, deep=True)
    lineage = analysis.lineage.model_copy(
        update={
            "artifact_id": new_id,
            "artifact_type": "analysis_artifact",
            "source_artifact_ids": [analysis_artifact_id, sql_artifact_id],
            "direct_source_artifact_ids": [analysis_artifact_id, sql_artifact_id],
            "provenance_ancestor_artifact_ids": [analysis_artifact_id],
        },
        deep=True,
    )
    manifest = analysis.manifest.model_copy(
        update={
            "artifact_id": new_id,
            "artifact_type": "analysis_artifact",
            "persistence_status": "not_persisted",
            "readback_verified": False,
            "linked_sql_artifact_id": sql_artifact_id,
            "report_input_snapshot_schema_version": snapshot.schema_version,
            "report_tabular_source_sql_artifact_id": snapshot.tabular_source_sql_artifact_id or "",
            "migrated_from_artifact_id": analysis_artifact_id,
            "migration_tool_version": MIGRATION_TOOL_VERSION,
            "migration_timestamp": timestamp,
            "lineage": lineage,
        },
        deep=True,
    )
    migrated = analysis.model_copy(
        update={
            "artifact_id": new_id,
            "payload": payload,
            "manifest": manifest,
            "lineage": lineage,
            "persistence_status": "not_persisted",
            "readback_verified": False,
            "produced_by_node": "offline_analysis_snapshot_migration",
        },
        deep=True,
    )
    if apply:
        migrated = commit_artifact(migrated)
    return AnalysisSnapshotMigrationResult(
        status="applied" if apply else "dry_run",
        source_analysis_artifact_id=analysis_artifact_id,
        source_sql_artifact_id=sql_artifact_id,
        migrated_artifact_id=new_id,
        write_performed=apply,
        envelope=migrated,
    )


def _load_explicit(
    *,
    thread_id: str,
    artifact_id: str,
    expected_type: str,
    auth_context: AuthContext,
) -> ArtifactEnvelope:
    access = resolve_target_artifact(
        thread_id=thread_id,
        artifact_id=artifact_id,
        auth=auth_context,
        expected_types={expected_type},
        expected_devices=[],
        require_complete_lineage=True,
    )
    if not access.allowed or access.record is None or access.record.artifact_envelope is None:
        raise PermissionError(access.code)
    return access.record.artifact_envelope


def _migration_artifact_id(analysis_id: str, sql_id: str, timestamp: str) -> str:
    digest = hashlib.sha256(f"{analysis_id}|{sql_id}|{timestamp}".encode("utf-8")).hexdigest()[:32]
    return f"art_analysis_migrated_{digest}"
