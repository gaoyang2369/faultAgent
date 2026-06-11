"""内存版诊断产物 store backend。"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta
from threading import RLock

from ..contracts import DiagnosisArtifactEnvelope
from .base import ArtifactStoreBackend


class MemoryArtifactStoreBackend(ArtifactStoreBackend):
    """带 TTL 与 LRU 的内存 backend。"""

    def __init__(self, *, max_entries: int = 200, ttl_seconds: int = 24 * 3600):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._artifacts: OrderedDict[str, list[tuple[datetime, DiagnosisArtifactEnvelope]]] = OrderedDict()
        self._entry_count = 0
        self._lock = RLock()

    def _is_expired(self, stored_at: datetime) -> bool:
        return datetime.now() - stored_at > timedelta(seconds=self.ttl_seconds)

    def _purge_expired_locked(self, thread_id: str | None = None) -> None:
        target_thread_ids = [thread_id] if thread_id is not None else list(self._artifacts.keys())
        for current_thread_id in target_thread_ids:
            entries = self._artifacts.get(current_thread_id)
            if not entries:
                continue
            fresh_entries = [(stored_at, envelope) for stored_at, envelope in entries if not self._is_expired(stored_at)]
            removed_count = len(entries) - len(fresh_entries)
            if removed_count > 0:
                self._entry_count -= removed_count
            if fresh_entries:
                self._artifacts[current_thread_id] = fresh_entries
            else:
                self._artifacts.pop(current_thread_id, None)

    def _evict_lru_locked(self) -> None:
        while self._entry_count > self.max_entries and self._artifacts:
            oldest_thread_id = next(iter(self._artifacts))
            entries = self._artifacts.get(oldest_thread_id) or []
            if not entries:
                self._artifacts.pop(oldest_thread_id, None)
                continue
            entries.pop(0)
            self._entry_count -= 1
            if entries:
                self._artifacts[oldest_thread_id] = entries
            else:
                self._artifacts.pop(oldest_thread_id, None)

    def save(self, envelope: DiagnosisArtifactEnvelope) -> DiagnosisArtifactEnvelope:
        with self._lock:
            self._purge_expired_locked(envelope.thread_id)
            entries = self._artifacts.get(envelope.thread_id, [])
            entries.append((datetime.now(), deepcopy(envelope)))
            self._artifacts[envelope.thread_id] = entries
            self._artifacts.move_to_end(envelope.thread_id)
            self._entry_count += 1
            self._evict_lru_locked()
            return deepcopy(entries[-1][1])

    def get_latest(self, thread_id: str) -> DiagnosisArtifactEnvelope | None:
        with self._lock:
            self._purge_expired_locked(thread_id)
            entries = self._artifacts.get(thread_id)
            if not entries:
                return None
            self._artifacts.move_to_end(thread_id)
            return deepcopy(entries[-1][1])

    def list_thread_artifacts(self, thread_id: str, limit: int = 20) -> list[DiagnosisArtifactEnvelope]:
        normalized_limit = max(1, limit)
        with self._lock:
            self._purge_expired_locked(thread_id)
            entries = self._artifacts.get(thread_id)
            if not entries:
                return []
            self._artifacts.move_to_end(thread_id)
            selected = list(reversed(entries))[:normalized_limit]
            return [deepcopy(envelope) for _, envelope in selected]

    def clear_thread(self, thread_id: str) -> None:
        with self._lock:
            entries = self._artifacts.pop(thread_id, [])
            self._entry_count -= len(entries)

    def clear_all(self) -> None:
        with self._lock:
            self._artifacts.clear()
            self._entry_count = 0

    def health_check(self) -> dict:
        with self._lock:
            thread_count = len(self._artifacts)
            entry_count = self._entry_count
        return {
            "status": "available",
            "backend": "memory",
            "thread_count": thread_count,
            "entry_count": entry_count,
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
        }
