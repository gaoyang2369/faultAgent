"""Workflow artifact store backend 抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..contracts import WorkflowArtifactEnvelope


class ArtifactStoreBackend(ABC):
    """线程级结构化产物存储 backend 接口。"""

    @abstractmethod
    def save(self, envelope: WorkflowArtifactEnvelope) -> WorkflowArtifactEnvelope:
        """保存一条结构化产物并返回拷贝。"""

    @abstractmethod
    def get_latest(self, thread_id: str) -> WorkflowArtifactEnvelope | None:
        """读取指定线程最近一次结构化产物。"""

    @abstractmethod
    def list_thread_artifacts(self, thread_id: str, limit: int = 20) -> list[WorkflowArtifactEnvelope]:
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
