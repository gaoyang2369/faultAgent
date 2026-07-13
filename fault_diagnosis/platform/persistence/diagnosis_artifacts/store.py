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

_BACKEND: ArtifactStoreBackend | None = None
_BACKEND_LOCK = RLock()


@dataclass(frozen=True)
class ArtifactLookupResult:
    envelope: DiagnosisArtifactEnvelope
    manifest: dict[str, Any]
    payload: Any


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
    for envelope in list_thread_artifacts(thread_id, limit=100):
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        manifests = payload.get("artifact_manifests")
        for manifest in manifests if isinstance(manifests, list) else []:
            if not isinstance(manifest, dict) or str(manifest.get("artifact_id") or "") != wanted:
                continue
            return ArtifactLookupResult(
                envelope=envelope,
                manifest=dict(manifest),
                payload=_payload_for_manifest(payload, manifest),
            )
    return None


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
