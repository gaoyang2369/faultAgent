"""会话历史索引 repository。"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from ..common.paths import RUN_STATE_DIR


class HistoryIndexRepository(Protocol):
    """历史索引 repository 协议。"""

    def record_thread(
        self,
        *,
        session_id: str,
        thread_id: str,
        history_type: str = "service",
        title: str | None = None,
    ) -> dict[str, Any]:
        """登记或刷新一个会话 thread。"""

    def list_thread_ids(self, *, session_id: str) -> list[str]:
        """按最近更新时间倒序返回当前 session 的 thread_id。"""

    def remove_thread(self, *, session_id: str, thread_id: str) -> None:
        """从索引中移除指定 thread。"""

    def health_check(self) -> dict[str, Any]:
        """返回 repository 轻量健康状态。"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_history_type(history_type: str | None) -> str:
    normalized = (history_type or "service").strip().lower()
    return normalized or "service"


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    session_id = str(entry.get("session_id") or "").strip()
    thread_id = str(entry.get("thread_id") or "").strip()
    if not session_id or not thread_id:
        return None
    created_at = int(entry.get("created_at") or entry.get("updated_at") or _now_ms())
    updated_at = int(entry.get("updated_at") or created_at)
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "history_type": _normalize_history_type(entry.get("history_type")),
        "title": str(entry.get("title") or "").strip(),
        "created_at": created_at,
        "updated_at": updated_at,
    }


class FileHistoryIndexRepository:
    """基于 JSON 文件的历史索引 repository。"""

    def __init__(self, path: str | os.PathLike[str] | None = None, *, max_entries: int = 10000) -> None:
        default_path = Path(RUN_STATE_DIR) / "history_index.json"
        self.path = Path(path or os.getenv("HISTORY_INDEX_PATH") or default_path)
        self.max_entries = max(100, int(max_entries))
        self._lock = RLock()

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_entries_unlocked(self) -> list[dict[str, Any]]:
        self._ensure_parent()
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(raw_entries, list):
            return []
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            entry = _normalize_entry(raw_entry)
            if not entry:
                continue
            key = (entry["session_id"], entry["thread_id"])
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
        return entries

    def _write_entries_unlocked(self, entries: list[dict[str, Any]]) -> None:
        self._ensure_parent()
        entries = sorted(entries, key=lambda item: int(item.get("updated_at") or 0), reverse=True)[: self.max_entries]
        payload = {
            "version": 1,
            "updated_at": _now_ms(),
            "entries": entries,
        }
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, self.path)

    def record_thread(
        self,
        *,
        session_id: str,
        thread_id: str,
        history_type: str = "service",
        title: str | None = None,
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        thread_id = str(thread_id or "").strip()
        if not session_id or not thread_id:
            raise ValueError("session_id 和 thread_id 不能为空")

        now = _now_ms()
        with self._lock:
            entries = self._read_entries_unlocked()
            existing: dict[str, Any] | None = None
            kept_entries: list[dict[str, Any]] = []
            for entry in entries:
                if entry["session_id"] == session_id and entry["thread_id"] == thread_id:
                    existing = entry
                    continue
                kept_entries.append(entry)
            updated_entry = {
                "session_id": session_id,
                "thread_id": thread_id,
                "history_type": _normalize_history_type(history_type),
                "title": str(title or (existing or {}).get("title") or "").strip(),
                "created_at": int((existing or {}).get("created_at") or now),
                "updated_at": now,
            }
            kept_entries.insert(0, updated_entry)
            self._write_entries_unlocked(kept_entries)
            return deepcopy(updated_entry)

    def list_thread_ids(self, *, session_id: str) -> list[str]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        with self._lock:
            entries = self._read_entries_unlocked()
        owned_entries = [entry for entry in entries if entry["session_id"] == session_id]
        owned_entries.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
        seen: set[str] = set()
        thread_ids: list[str] = []
        for entry in owned_entries:
            thread_id = entry["thread_id"]
            if thread_id in seen:
                continue
            seen.add(thread_id)
            thread_ids.append(thread_id)
        return thread_ids

    def remove_thread(self, *, session_id: str, thread_id: str) -> None:
        session_id = str(session_id or "").strip()
        thread_id = str(thread_id or "").strip()
        if not session_id or not thread_id:
            return
        with self._lock:
            entries = self._read_entries_unlocked()
            kept_entries = [
                entry for entry in entries
                if not (entry["session_id"] == session_id and entry["thread_id"] == thread_id)
            ]
            if len(kept_entries) != len(entries):
                self._write_entries_unlocked(kept_entries)

    def health_check(self) -> dict[str, Any]:
        try:
            self._ensure_parent()
            test_path = self.path.parent / ".history-index-healthcheck.tmp"
            test_path.write_text("ok", encoding="utf-8")
            test_path.unlink(missing_ok=True)
            entries = self._read_entries_unlocked()
            return {
                "status": "available",
                "backend": "file",
                "path": str(self.path),
                "exists": self.path.exists(),
                "entry_count": len(entries),
                "writable": True,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "backend": "file",
                "path": str(self.path),
                "writable": False,
                "detail": str(exc),
            }


class MemoryHistoryIndexRepository:
    """测试用内存历史索引 repository。"""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = RLock()

    def record_thread(
        self,
        *,
        session_id: str,
        thread_id: str,
        history_type: str = "service",
        title: str | None = None,
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        thread_id = str(thread_id or "").strip()
        if not session_id or not thread_id:
            raise ValueError("session_id 和 thread_id 不能为空")
        now = _now_ms()
        with self._lock:
            existing = None
            kept_entries = []
            for entry in self._entries:
                if entry["session_id"] == session_id and entry["thread_id"] == thread_id:
                    existing = entry
                    continue
                kept_entries.append(entry)
            updated_entry = {
                "session_id": session_id,
                "thread_id": thread_id,
                "history_type": _normalize_history_type(history_type),
                "title": str(title or (existing or {}).get("title") or "").strip(),
                "created_at": int((existing or {}).get("created_at") or now),
                "updated_at": now,
            }
            kept_entries.insert(0, updated_entry)
            self._entries = kept_entries
            return deepcopy(updated_entry)

    def list_thread_ids(self, *, session_id: str) -> list[str]:
        session_id = str(session_id or "").strip()
        with self._lock:
            entries = deepcopy(self._entries)
        entries = [entry for entry in entries if entry["session_id"] == session_id]
        entries.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
        return [entry["thread_id"] for entry in entries]

    def remove_thread(self, *, session_id: str, thread_id: str) -> None:
        with self._lock:
            self._entries = [
                entry for entry in self._entries
                if not (entry["session_id"] == session_id and entry["thread_id"] == thread_id)
            ]

    def health_check(self) -> dict[str, Any]:
        with self._lock:
            entry_count = len(self._entries)
        return {
            "status": "available",
            "backend": "memory",
            "entry_count": entry_count,
        }


_REPOSITORY: HistoryIndexRepository | None = None
_REPOSITORY_LOCK = RLock()


def configure_history_index_repository(repository: HistoryIndexRepository) -> HistoryIndexRepository:
    """显式注入历史索引 repository。"""

    global _REPOSITORY
    with _REPOSITORY_LOCK:
        _REPOSITORY = repository
        return _REPOSITORY


def reset_history_index_repository() -> None:
    """重置历史索引 repository 缓存。"""

    global _REPOSITORY
    with _REPOSITORY_LOCK:
        _REPOSITORY = None


def get_history_index_repository() -> HistoryIndexRepository:
    """获取当前历史索引 repository。"""

    global _REPOSITORY
    with _REPOSITORY_LOCK:
        if _REPOSITORY is None:
            if os.getenv("PYTEST_CURRENT_TEST"):
                _REPOSITORY = MemoryHistoryIndexRepository()
            else:
                _REPOSITORY = FileHistoryIndexRepository()
        return _REPOSITORY


def record_history_thread(
    *,
    session_id: str,
    thread_id: str,
    history_type: str = "service",
    title: str | None = None,
) -> dict[str, Any]:
    """登记历史 thread 的兼容 facade。"""

    return get_history_index_repository().record_thread(
        session_id=session_id,
        thread_id=thread_id,
        history_type=history_type,
        title=title,
    )


def remove_history_thread(*, session_id: str, thread_id: str) -> None:
    """移除历史 thread 的兼容 facade。"""

    get_history_index_repository().remove_thread(session_id=session_id, thread_id=thread_id)
