"""线程级诊断结构化产物存储 facade。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from threading import RLock

from .backends import (
    ArtifactStoreBackend,
    FileArtifactStoreBackend,
    MemoryArtifactStoreBackend,
    PostgresArtifactStoreBackend,
)
from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.domain.artifacts import ArtifactEnvelope, ArtifactManifest

_BACKEND: ArtifactStoreBackend | None = None
_BACKEND_LOCK = RLock()


@dataclass(frozen=True)
class ArtifactLookupResult:
    envelope: DiagnosisArtifactEnvelope
    manifest: dict[str, Any]
    payload: Any
    artifact_envelope: "ArtifactEnvelope | None" = None


def _resolve_default_backend_name() -> str:
    explicit = (
        os.getenv("DIAGNOSIS_ARTIFACT_BACKEND")
        or os.getenv("WORKFLOW_ARTIFACT_BACKEND")
        or ""
    ).strip().lower()
    if explicit:
        return explicit
    if os.getenv("PYTEST_CURRENT_TEST"):
        return "memory"
    return "file"


def _build_backend_from_env() -> ArtifactStoreBackend:
    backend_name = _resolve_default_backend_name()
    if backend_name == "memory":
        return MemoryArtifactStoreBackend()
    if backend_name in {"file", "filesystem", "fs"}:
        return FileArtifactStoreBackend()
    if backend_name == "postgres":
        table_name = (
            os.getenv("DIAGNOSIS_ARTIFACT_TABLE")
            or os.getenv("WORKFLOW_ARTIFACT_TABLE")
            or "diagnosis_artifacts"
        ).strip() or "diagnosis_artifacts"
        dsn = (
            os.getenv("DIAGNOSIS_ARTIFACT_POSTGRES_DSN")
            or os.getenv("WORKFLOW_ARTIFACT_POSTGRES_DSN")
            or ""
        ).strip() or None
        return PostgresArtifactStoreBackend(dsn=dsn, table_name=table_name)
    raise ValueError(f"不支持的 diagnosis artifact backend：{backend_name}")


def configure_artifact_store_backend(backend: ArtifactStoreBackend) -> ArtifactStoreBackend:
    """显式注入 artifact store backend。"""

    global _BACKEND
    with _BACKEND_LOCK:
        _BACKEND = backend
        _register_domain_artifact_lister()
        return _BACKEND


def reset_artifact_store_backend() -> None:
    """重置缓存 backend，下次使用时按环境变量重新创建。"""

    global _BACKEND
    with _BACKEND_LOCK:
        _BACKEND = None


def get_artifact_store_backend() -> ArtifactStoreBackend:
    """获取当前生效的 artifact store backend。"""

    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = _build_backend_from_env()
            _register_domain_artifact_lister()
        return _BACKEND


def save_thread_artifact(envelope: DiagnosisArtifactEnvelope) -> DiagnosisArtifactEnvelope:
    """按 thread_id 保存一条结构化产物。"""

    return get_artifact_store_backend().save(envelope)


def commit_artifact(envelope: "ArtifactEnvelope") -> "ArtifactEnvelope":
    """Local staged commit with exact readback before an artifact may be published."""

    backend = get_artifact_store_backend()
    staged_manifest = envelope.manifest.model_copy(update={"persistence_status": "staged", "readback_verified": False})
    staged = envelope.model_copy(
        update={"persistence_status": "staged", "readback_verified": False, "manifest": staged_manifest}, deep=True
    )
    backend.save_artifact(staged)
    _validate_for_commit(staged)
    committed_manifest = staged.manifest.model_copy(update={"persistence_status": "committed"})
    committed = staged.model_copy(update={"persistence_status": "committed", "manifest": committed_manifest}, deep=True)
    backend.save_artifact(committed)
    readback = backend.get_artifact(committed.thread_id, committed.artifact_id)
    if readback is None:
        raise RuntimeError("artifact_exact_readback_failed")
    if (
        readback.thread_id != committed.thread_id
        or readback.artifact_id != committed.artifact_id
        or readback.manifest.artifact_id != committed.artifact_id
        or readback.status != "complete"
        or readback.persistence_status != "committed"
    ):
        raise RuntimeError("artifact_exact_readback_identity_mismatch")
    verified_manifest = readback.manifest.model_copy(update={"readback_verified": True})
    verified = readback.model_copy(update={"readback_verified": True, "manifest": verified_manifest}, deep=True)
    backend.save_artifact(verified)
    final = backend.get_artifact(verified.thread_id, verified.artifact_id)
    if final is None or not final.readback_verified:
        raise RuntimeError("artifact_verified_readback_failed")
    return final


def get_canonical_artifact(thread_id: str, artifact_id: str) -> "ArtifactEnvelope | None":
    """Exact canonical read; never falls back to latest."""

    wanted = str(artifact_id or "").strip()
    if not thread_id or not wanted:
        return None
    return get_artifact_store_backend().get_artifact(thread_id, wanted)


def get_thread_artifact(thread_id: str) -> DiagnosisArtifactEnvelope | None:
    """读取指定 thread_id 最近一次结构化产物。"""

    return get_artifact_store_backend().get_latest(thread_id)


def list_thread_artifacts(thread_id: str, limit: int = 20) -> list[DiagnosisArtifactEnvelope]:
    """读取指定 thread_id 最近若干条结构化产物。"""

    return get_artifact_store_backend().list_thread_artifacts(thread_id, limit=limit)


def get_artifact_by_id(thread_id: str, artifact_id: str) -> ArtifactLookupResult | None:
    """Resolve one manifest and its typed payload without a latest-artifact fallback."""

    wanted = str(artifact_id or "").strip()
    if not thread_id or not wanted:
        return None
    canonical = get_canonical_artifact(thread_id, wanted)
    if canonical is not None:
        compat = _compat_parent_for_artifact(thread_id, wanted) or _compat_envelope_for_canonical(canonical)
        return ArtifactLookupResult(
            envelope=compat,
            manifest=canonical.manifest.model_dump(mode="json", exclude_none=True),
            payload=canonical.payload,
            artifact_envelope=canonical,
        )
    for envelope in list_thread_artifacts(thread_id, limit=100):
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        manifests = payload.get("artifact_manifests")
        for manifest in manifests if isinstance(manifests, list) else []:
            if not isinstance(manifest, dict) or str(manifest.get("artifact_id") or "") != wanted:
                continue
            upgraded = _upgrade_legacy_artifact(envelope, manifest, _payload_for_manifest(payload, manifest))
            if upgraded.status == "complete":
                try:
                    upgraded = commit_artifact(upgraded)
                except Exception:
                    upgraded = upgraded.model_copy(update={"persistence_status": "failed", "readback_verified": False})
            return ArtifactLookupResult(
                envelope=envelope,
                manifest=upgraded.manifest.model_dump(mode="json", exclude_none=True),
                payload=upgraded.payload,
                artifact_envelope=upgraded,
            )
    return None


def get_artifact_manifest_exact(thread_id: str, artifact_id: str) -> dict[str, Any] | None:
    record = get_artifact_by_id(thread_id, artifact_id)
    return dict(record.manifest) if record is not None else None


def _compat_parent_for_artifact(thread_id: str, artifact_id: str) -> DiagnosisArtifactEnvelope | None:
    for envelope in list_thread_artifacts(thread_id, limit=100):
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        manifests = payload.get("artifact_manifests") if isinstance(payload.get("artifact_manifests"), list) else []
        if any(isinstance(item, dict) and str(item.get("artifact_id") or "") == artifact_id for item in manifests):
            return envelope
    return None


def _upgrade_legacy_artifact(
    envelope: DiagnosisArtifactEnvelope,
    raw_manifest: dict[str, Any],
    raw_payload: Any,
) -> "ArtifactEnvelope":
    """Upgrade only from structured proof; never infer lineage from prose."""

    manifest = ArtifactManifest.model_validate(raw_manifest)
    payload = raw_payload if isinstance(raw_payload, dict) else {"legacy_payload": raw_payload}
    lineage = manifest.lineage.model_copy(
        update={
            "artifact_id": manifest.artifact_id,
            "artifact_type": manifest.artifact_type,
        }
    )
    proof = _legacy_structured_proof(manifest, payload)
    status = "complete" if proof else "legacy_partial"
    if proof:
        manifest = manifest.model_copy(update={"artifact_status": "complete", "lineage": lineage})
    else:
        lineage = lineage.model_copy(update={"lineage_status": "legacy_partial"})
        manifest = manifest.model_copy(update={
            "artifact_status": "legacy_partial",
            "followupable": False,
            "reportable": False,
            "actionable": False,
            "lineage": lineage,
        })
    return ArtifactEnvelope(
        artifact_id=manifest.artifact_id,
        artifact_type=manifest.artifact_type,
        owner_user_id=manifest.owner_user_id,
        owner_session_id=manifest.owner_session_id,
        thread_id=envelope.thread_id,
        request_id=manifest.request_id,
        trace_id=manifest.trace_id,
        turn_id=manifest.turn_id,
        produced_by_node=(manifest.produced_by_nodes or [""])[0],
        payload=payload,
        manifest=manifest,
        lineage=lineage,
        status=status,
    )


def _legacy_structured_proof(manifest: "ArtifactManifest", payload: dict[str, Any]) -> bool:
    if manifest.lineage.lineage_status != "complete" or not manifest.artifact_id:
        return False
    artifact_type = manifest.artifact_type
    if artifact_type == "sql_artifact":
        sql = payload.get("sql_artifact") if isinstance(payload.get("sql_artifact"), dict) else payload
        return bool(manifest.device_refs and sql.get("source_table") and manifest.evidence_refs)
    if artifact_type == "knowledge_artifact":
        knowledge = payload.get("knowledge_artifact") if isinstance(payload.get("knowledge_artifact"), dict) else payload
        return bool(knowledge.get("fault_code_entries") and manifest.evidence_refs)
    if artifact_type in {"analysis_artifact", "structured_analysis_artifact"}:
        return bool(manifest.device_refs and manifest.lineage.source_artifact_ids and manifest.evidence_refs)
    if artifact_type in {"comparison_artifact", "report_artifact", "workorder_artifact"}:
        return bool(manifest.lineage.source_artifact_ids and manifest.device_refs)
    return False


def _validate_for_commit(envelope: ArtifactEnvelope) -> None:
    if not envelope.thread_id or not envelope.artifact_id:
        raise ValueError("artifact ownership requires thread_id and artifact_id")
    if envelope.status != "complete" or envelope.lineage.lineage_status != "complete":
        raise ValueError("only complete artifacts may be committed")
    if not envelope.payload:
        raise ValueError("artifact payload must not be empty")
    if envelope.manifest.thread_id != envelope.thread_id:
        raise ValueError("manifest thread ownership mismatch")
    if envelope.manifest.lineage != envelope.lineage:
        raise ValueError("manifest lineage must equal envelope lineage")


def _compat_envelope_for_canonical(canonical: "ArtifactEnvelope") -> DiagnosisArtifactEnvelope:
    from datetime import UTC, datetime
    from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactType

    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.FAULT_DIAGNOSIS,
        thread_id=canonical.thread_id,
        created_at=datetime.now(UTC).isoformat(),
        request_summary=canonical.artifact_type,
        final_answer="",
        report_filename=canonical.manifest.report_filename or canonical.manifest.report_url or None,
        payload={
            "artifact_manifests": [canonical.manifest.model_dump(mode="json", exclude_none=True)],
            "artifacts_by_id": {canonical.artifact_id: canonical.payload},
        },
    )


def _payload_for_manifest(payload: dict[str, Any], manifest: dict[str, Any]) -> Any:
    artifact_id = str(manifest.get("artifact_id") or "")
    registry = payload.get("artifacts_by_id")
    if isinstance(registry, dict) and artifact_id in registry:
        return registry[artifact_id]
    key_by_type = {
        "sql_artifact": "sql_artifact",
        "knowledge_artifact": "knowledge_artifact",
        "analysis_artifact": "analysis_artifact",
        "structured_analysis_artifact": "structured_analysis_artifact",
        "comparison_artifact": "comparison_artifact",
        "report_artifact": "report_artifact",
        "workorder_artifact": "workorder_draft",
    }
    return payload.get(key_by_type.get(str(manifest.get("artifact_type") or ""), ""))


def _register_domain_artifact_lister() -> None:
    try:
        from fault_diagnosis.domain.context import set_default_artifact_lister

        set_default_artifact_lister(lambda thread_id, limit: list_thread_artifacts(thread_id, limit=limit))
    except Exception:
        return


def clear_thread_artifact(thread_id: str) -> None:
    """清理指定 thread_id 的结构化产物。"""

    get_artifact_store_backend().clear_thread(thread_id)


def clear_all_artifacts() -> None:
    """清理全部线程级结构化产物。"""

    get_artifact_store_backend().clear_all()


def check_artifact_store_health() -> dict[str, Any]:
    """返回当前 artifact store 的轻量健康状态。"""

    try:
        backend = get_artifact_store_backend()
        return backend.health_check()
    except Exception as exc:
        return {
            "status": "failed",
            "backend": "unknown",
            "detail": str(exc),
        }
