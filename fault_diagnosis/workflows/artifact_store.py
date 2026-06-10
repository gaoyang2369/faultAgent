"""线程级 Workflow 结构化产物存储 facade。"""

from __future__ import annotations

import os
from typing import Any
from threading import RLock

from .artifact_backends import (
    ArtifactStoreBackend,
    FileArtifactStoreBackend,
    MemoryArtifactStoreBackend,
    PostgresArtifactStoreBackend,
)
from .contracts import WorkflowArtifactEnvelope

_BACKEND: ArtifactStoreBackend | None = None
_BACKEND_LOCK = RLock()


def _resolve_default_backend_name() -> str:
    explicit = (os.getenv("WORKFLOW_ARTIFACT_BACKEND") or "").strip().lower()
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
        table_name = (os.getenv("WORKFLOW_ARTIFACT_TABLE") or "workflow_artifacts").strip() or "workflow_artifacts"
        dsn = (os.getenv("WORKFLOW_ARTIFACT_POSTGRES_DSN") or "").strip() or None
        return PostgresArtifactStoreBackend(dsn=dsn, table_name=table_name)
    raise ValueError(f"不支持的 workflow artifact backend：{backend_name}")


def configure_artifact_store_backend(backend: ArtifactStoreBackend) -> ArtifactStoreBackend:
    """显式注入 artifact store backend。"""

    global _BACKEND
    with _BACKEND_LOCK:
        _BACKEND = backend
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
        return _BACKEND


def save_thread_artifact(envelope: WorkflowArtifactEnvelope) -> WorkflowArtifactEnvelope:
    """按 thread_id 保存一条结构化产物。"""

    return get_artifact_store_backend().save(envelope)


def get_thread_artifact(thread_id: str) -> WorkflowArtifactEnvelope | None:
    """读取指定 thread_id 最近一次结构化产物。"""

    return get_artifact_store_backend().get_latest(thread_id)


def list_thread_artifacts(thread_id: str, limit: int = 20) -> list[WorkflowArtifactEnvelope]:
    """读取指定 thread_id 最近若干条结构化产物。"""

    return get_artifact_store_backend().list_thread_artifacts(thread_id, limit=limit)


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
