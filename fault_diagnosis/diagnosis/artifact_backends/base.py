"""诊断产物 store backend 抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..contracts import DiagnosisArtifactEnvelope


class ArtifactStoreBackend(ABC):
    """线程级结构化产物存储 backend 接口。"""

    @abstractmethod
    def save(self, envelope: DiagnosisArtifactEnvelope) -> DiagnosisArtifactEnvelope:
        """保存一条结构化产物并返回拷贝。"""

    @abstractmethod
    def get_latest(self, thread_id: str) -> DiagnosisArtifactEnvelope | None:
        """读取指定线程最近一次结构化产物。"""

    @abstractmethod
    def list_thread_artifacts(self, thread_id: str, limit: int = 20) -> list[DiagnosisArtifactEnvelope]:
        """读取指定线程最近若干条结构化产物。"""

    @abstractmethod
    def clear_thread(self, thread_id: str) -> None:
        """清理指定线程的结构化产物。"""

    @abstractmethod
    def clear_all(self) -> None:
        """清理全部结构化产物。"""

    def health_check(self) -> dict[str, Any]:
        """返回 backend 轻量健康状态。"""

        return {
            "status": "available",
            "backend": self.__class__.__name__,
        }
