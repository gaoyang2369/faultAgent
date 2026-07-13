"""Strict authorization and lineage validation for targeted artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from fault_diagnosis.domain.security.assets import asset_is_in_scope
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import ArtifactLookupResult, get_artifact_by_id


@dataclass(frozen=True)
class ArtifactAccessResult:
    allowed: bool
    code: str
    record: ArtifactLookupResult | None = None


def resolve_target_artifact(
    *,
    thread_id: str,
    artifact_id: str,
    auth: AuthContext,
    expected_types: set[str],
    expected_devices: list[str],
    require_complete_lineage: bool,
) -> ArtifactAccessResult:
    record = get_artifact_by_id(thread_id, artifact_id)
    if record is None:
        return ArtifactAccessResult(False, "target_artifact_not_found")
    manifest = record.manifest
    artifact_type = str(manifest.get("artifact_type") or "")
    if expected_types and artifact_type not in expected_types:
        return ArtifactAccessResult(False, "target_artifact_type_mismatch")
    owner_user_id = str(manifest.get("owner_user_id") or "")
    owner_session_id = str(manifest.get("owner_session_id") or "")
    if owner_user_id and owner_user_id != auth.user_id and not auth.is_admin():
        return ArtifactAccessResult(False, "target_artifact_owner_mismatch")
    if owner_session_id and auth.session_id and owner_session_id != auth.session_id and not auth.is_admin():
        return ArtifactAccessResult(False, "target_artifact_session_mismatch")
    lineage = manifest.get("lineage") if isinstance(manifest.get("lineage"), dict) else {}
    lineage_status = str(lineage.get("lineage_status") or "legacy_partial")
    subjects = _texts(lineage.get("subject_device_refs") or manifest.get("device_refs"))
    if require_complete_lineage and lineage_status != "complete":
        return ArtifactAccessResult(False, "target_artifact_lineage_incomplete")
    if lineage_status == "invalid":
        return ArtifactAccessResult(False, "target_artifact_lineage_invalid")
    if expected_devices and subjects and set(subjects) != set(expected_devices):
        return ArtifactAccessResult(False, "target_artifact_subject_mismatch")
    if any(not (auth.is_admin() or asset_is_in_scope(device, auth.asset_scope)) for device in subjects):
        return ArtifactAccessResult(False, "target_artifact_device_out_of_scope")
    source_tables = _texts(lineage.get("source_tables") or manifest.get("source_table"))
    if source_tables and not auth.is_admin() and any(table not in set(auth.table_scope) for table in source_tables):
        return ArtifactAccessResult(False, "target_artifact_table_out_of_scope")
    return ArtifactAccessResult(True, "ok", record)


def _texts(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)] if str(value or "").strip() else []

