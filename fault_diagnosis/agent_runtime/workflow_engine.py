"""Workflow 路径的 SSE 流适配引擎。"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Callable

from fastapi import FastAPI

from .stream_control import StreamCancellationHandle
from .sse_adapter import adapt_sse_chunk


class WorkflowStreamEngine:
    """封装 workflow/dev 已有 SSE chunk 到统一 adapter 的转换。"""

    def __init__(
        self,
        *,
        workflow_streamer: Callable[..., AsyncGenerator[str, None]],
        dev_streamer: Callable[..., AsyncGenerator[str, None]],
    ) -> None:
        self.workflow_streamer = workflow_streamer
        self.dev_streamer = dev_streamer

    async def stream_workflow(
        self,
        app: FastAPI,
        message: str,
        thread_id: str,
        user_identity: str,
        *,
        request_id: str,
        stream_id: str,
        trace_id: str,
        cancel_handle: StreamCancellationHandle | None,
    ) -> AsyncGenerator[str, None]:
        """适配 workflow runner 已输出的 SSE 帧。"""

        async for chunk in self.workflow_streamer(
            app,
            message,
            thread_id,
            user_identity,
            request_id=request_id,
            stream_id=stream_id,
            cancel_handle=cancel_handle,
        ):
            yield adapt_sse_chunk(chunk, trace_id, thread_id=thread_id)

    async def stream_dev(
        self,
        app: FastAPI,
        message: str,
        thread_id: str,
        user_identity: str,
        *,
        trace_id: str,
        cancel_event: Any = None,
    ) -> AsyncGenerator[str, None]:
        """适配本地开发模式的模拟 SSE 帧。"""

        async for chunk in self.dev_streamer(
            app,
            message,
            thread_id,
            user_identity,
            cancel_event=cancel_event,
        ):
            yield adapt_sse_chunk(chunk, trace_id, thread_id=thread_id)
