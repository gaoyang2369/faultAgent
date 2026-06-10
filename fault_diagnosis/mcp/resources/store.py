"""MCP 资源缓存：进程内内存存储，带 TTL 与容量控制。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
import time
from typing import Any

DEFAULT_RESOURCE_TTL_SECONDS = 3600
MAX_ITEMS_PER_RESOURCE = 128


@dataclass(slots=True)
class _ResourceEntry:
    """单条资源缓存记录。"""

    content: Any
    stored_at: float
    expires_at: float


_STORE: dict[str, dict[str, _ResourceEntry]] = {}
_LOCK = RLock()


def _now_ts() -> float:
    return time.time()


def _prune_expired_entries(name: str, now_ts: float | None = None) -> None:
    resource_entries = _STORE.get(name)
    if not resource_entries:
        return

    now_ts = _now_ts() if now_ts is None else now_ts
    expired_keys = [key for key, entry in resource_entries.items() if entry.expires_at <= now_ts]
    for key in expired_keys:
        resource_entries.pop(key, None)

    if not resource_entries:
        _STORE.pop(name, None)


def _enforce_capacity(name: str) -> None:
    resource_entries = _STORE.get(name)
    if not resource_entries or len(resource_entries) <= MAX_ITEMS_PER_RESOURCE:
        return

    overflow = len(resource_entries) - MAX_ITEMS_PER_RESOURCE
    oldest_keys = sorted(resource_entries.items(), key=lambda item: item[1].stored_at)[:overflow]
    for key, _ in oldest_keys:
        resource_entries.pop(key, None)

    if not resource_entries:
        _STORE.pop(name, None)


def put_resource_content(name: str, key: str, content: Any, *, ttl_seconds: int = DEFAULT_RESOURCE_TTL_SECONDS) -> None:
    """写入资源缓存。"""

    if not name or not key:
        return

    now_ts = _now_ts()
    ttl_seconds = max(1, int(ttl_seconds))
    with _LOCK:
        _prune_expired_entries(name, now_ts)
        _STORE.setdefault(name, {})[key] = _ResourceEntry(
            content=deepcopy(content),
            stored_at=now_ts,
            expires_at=now_ts + ttl_seconds,
        )
        _enforce_capacity(name)


def get_resource_content(name: str, key: str) -> Any | None:
    """读取资源缓存；过期条目会在读取时惰性清理。"""

    if not name or not key:
        return None

    now_ts = _now_ts()
    with _LOCK:
        _prune_expired_entries(name, now_ts)
        entry = _STORE.get(name, {}).get(key)
        if entry is None:
            return None
        return deepcopy(entry.content)


def list_resource_keys(name: str) -> list[str]:
    """列出当前仍有效的资源 key。"""

    if not name:
        return []

    now_ts = _now_ts()
    with _LOCK:
        _prune_expired_entries(name, now_ts)
        resource_entries = _STORE.get(name, {})
        return [
            key
            for key, _ in sorted(
                resource_entries.items(),
                key=lambda item: item[1].stored_at,
                reverse=True,
            )
        ]


def clear_resource_content(name: str | None = None) -> None:
    """清空资源缓存；测试与本地验收脚本可复用。"""

    with _LOCK:
        if name:
            _STORE.pop(name, None)
            return
        _STORE.clear()


def get_resource_store_policy() -> dict[str, Any]:
    """返回 resource store 的运行策略描述。"""

    return {
        "mode": "process_memory",
        "ttl_seconds": DEFAULT_RESOURCE_TTL_SECONDS,
        "max_items_per_resource": MAX_ITEMS_PER_RESOURCE,
        "cleanup": "lazy_on_put_get_list",
        "shared_across_processes": False,
        "persistent_across_restart": False,
    }
