"""流式请求取消注册表。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from ..common.logger import get_logger
from ..common.utils import summarize_identifier_for_log

_log = get_logger("stream_control")


@dataclass
class StreamCancellationHandle:
    stream_id: str
    request_id: str
    thread_id: str
    session_id: str
    created_at: float = field(default_factory=time.monotonic)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_reason: str | None = None

    def cancel(self, reason: str = "user_stop") -> bool:
        if self.cancel_event.is_set():
            return False
        self.cancel_reason = reason
        self.cancel_event.set()
        return True


def _ensure_registry_state(app: FastAPI) -> tuple[dict[str, StreamCancellationHandle], asyncio.Lock]:
    registry = getattr(app.state, "stream_cancel_registry", None)
    lock = getattr(app.state, "stream_cancel_registry_lock", None)
    if registry is None:
        registry = {}
        app.state.stream_cancel_registry = registry
    if lock is None:
        lock = asyncio.Lock()
        app.state.stream_cancel_registry_lock = lock
    return registry, lock


async def register_stream_handle(
    app: FastAPI,
    *,
    stream_id: str,
    request_id: str,
    thread_id: str,
    session_id: str,
) -> StreamCancellationHandle:
    registry, lock = _ensure_registry_state(app)
    handle = StreamCancellationHandle(
        stream_id=stream_id,
        request_id=request_id,
        thread_id=thread_id,
        session_id=session_id,
    )
    async with lock:
        registry[stream_id] = handle
    _log.info(
        "已注册流式取消句柄",
        stream_id=summarize_identifier_for_log(stream_id, keep=8),
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        session_id=summarize_identifier_for_log(session_id, keep=8),
    )
    return handle


async def cancel_stream_handle(
    app: FastAPI,
    *,
    stream_id: str,
    session_id: str,
    reason: str = "user_stop",
) -> tuple[str, StreamCancellationHandle | None]:
    registry, lock = _ensure_registry_state(app)
    async with lock:
        handle = registry.get(stream_id)
        if handle is None:
            return "not_found", None
        if handle.session_id != session_id:
            return "forbidden", None
        changed = handle.cancel(reason)
        return ("cancelled" if changed else "already_cancelled"), handle


async def clear_stream_handle(app: FastAPI, stream_id: str) -> None:
    registry, lock = _ensure_registry_state(app)
    async with lock:
        handle = registry.pop(stream_id, None)
    if handle is not None:
        _log.info(
            "已清理流式取消句柄",
            stream_id=summarize_identifier_for_log(stream_id, keep=8),
            thread_id=summarize_identifier_for_log(handle.thread_id, keep=10),
            cancel_reason=handle.cancel_reason,
            lifetime_ms=round((time.monotonic() - handle.created_at) * 1000, 1),
        )


def build_stream_stop_payload(status: str, handle: StreamCancellationHandle | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": status in {"cancelled", "already_cancelled"},
        "status": status,
    }
    if handle is not None:
        payload.update(
            {
                "stream_id": handle.stream_id,
                "thread_id": handle.thread_id,
                "cancel_reason": handle.cancel_reason,
            }
        )
    return payload
