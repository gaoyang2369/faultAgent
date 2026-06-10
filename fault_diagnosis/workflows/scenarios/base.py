"""场景级 Workflow Runner 基类。"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncGenerator

from ...agent_runtime.error_classification import classify_model_gateway_error, model_error_code
from ...common.utils import safe_json_dumps
from ..contracts import WorkflowRunResult, WorkflowStepResult

# 心跳默认间隔；单个 LLM / SQL await 超过该时长会在阻塞期间周期性发送 ping 帧
_PING_INTERVAL_SECONDS = 10.0


class BaseScenarioRunner(ABC):
    """约束各场景 Runner 的公共接口。"""

    def __init__(self, message: str, thread_id: str, user_identity: str = "游客"):
        self.message = message
        self.thread_id = thread_id
        self.user_identity = user_identity
        self.steps: list[WorkflowStepResult] = []
        self.cancel_handle: Any = None
        self._last_step_result: Any = None
        self.route_result: Any = None

    def _iso_now(self) -> str:
        return datetime.now().isoformat()

    def _record_step(
        self,
        *,
        step_name: str,
        status: str,
        summary: str,
        started_at: str,
        error: str | None = None,
    ) -> None:
        self.steps.append(
            WorkflowStepResult(
                step_name=step_name,
                status=status,
                summary=summary,
                error=error,
                started_at=started_at,
                finished_at=self._iso_now(),
            )
        )

    def _is_cancelled(self) -> bool:
        """检测 stop-gen 信号是否已触发。"""

        event = getattr(self.cancel_handle, "cancel_event", None)
        return bool(event is not None and event.is_set())

    def _cancel_reason(self) -> str:
        return getattr(self.cancel_handle, "cancel_reason", None) or "cancelled"

    def _build_ping_frame(self, *, stage: str = "reasoning", message: str | None = None) -> str:
        """复用与旧链路一致的 ping 事件格式，避免前端需要新增处理分支。"""

        default_message = (
            "模型仍在推理，尚未产出可显示内容..."
            if stage == "reasoning"
            else "长任务处理中，连接保持中..."
        )
        payload = {
            "type": "ping",
            "timestamp": self._iso_now(),
            "stage": stage,
            "message": message or default_message,
        }
        return f"event: ping\ndata: {safe_json_dumps(payload)}\n\n"

    def _build_cancel_complete_frame(self) -> str:
        """用户触发 stop-gen 时输出的终态 complete 帧。"""

        payload = {
            "type": "chat_complete",
            "thread_id": self.thread_id,
            "cancelled": True,
            "cancel_reason": self._cancel_reason(),
            "final_content": "",
            "todos": [],
            "timestamp": self._iso_now(),
        }
        return f"event: complete\ndata: {safe_json_dumps(payload)}\n\n"

    def _build_server_error_payload(self, *, error_id: str, error: Exception) -> dict[str, Any]:
        """构造 Workflow 场景的安全错误事件，同时保留可操作的模型网关提示。"""

        model_gateway_error = classify_model_gateway_error(error)
        if model_gateway_error:
            category, message = model_gateway_error
            return {
                "type": "error",
                "message": message,
                "error_id": error_id,
                "error_category": category,
                "code": model_error_code(category),
                "retryable": False,
            }

        return {
            "type": "error",
            "message": "请求处理失败，请稍后重试",
            "error_id": error_id,
            "error_category": "internal",
            "code": "INTERNAL_ERROR",
            "retryable": False,
        }

    def _route_payload(self) -> dict[str, Any] | None:
        route_result = getattr(self, "route_result", None)
        if route_result is None:
            return None
        if hasattr(route_result, "model_dump"):
            return route_result.model_dump()
        if isinstance(route_result, dict):
            return dict(route_result)
        return None

    async def _drive_step(
        self,
        coro: Any,
        *,
        stage: str = "reasoning",
        interval: float = _PING_INTERVAL_SECONDS,
    ) -> AsyncGenerator[str, None]:
        """驱动单个阻塞协程执行，阻塞期间每 interval 秒 yield 一次 ping 帧。

        协程完成后把返回值写入 self._last_step_result。外层场景 Runner 读取该字段即可。
        若协程抛出异常，照常向外抛出；ping 计时器会在 finally 中取消后台任务。
        """

        task = asyncio.create_task(coro)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    self._last_step_result = task.result()
                    return
                yield self._build_ping_frame(stage=stage)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

    @abstractmethod
    async def run(self) -> WorkflowRunResult:
        """执行场景主链路。"""

    @abstractmethod
    async def stream_events(
        self,
        app: Any,
        *,
        request_id: str | None = None,
        stream_id: str | None = None,
        cancel_handle: Any = None,
    ) -> AsyncGenerator[str, None]:
        """按当前 SSE 契约输出场景执行事件。"""
