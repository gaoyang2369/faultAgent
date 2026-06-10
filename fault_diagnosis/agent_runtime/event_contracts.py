"""聊天流内部事件模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatEventModel(BaseModel):
    """聊天流事件模型基类，允许兼容字段继续透传。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ChatStartEvent(ChatEventModel):
    """聊天开始事件。"""

    type: Literal["chat_start"] = "chat_start"
    thread_id: str = Field(description="后端最终采用的会话线程")
    stream_id: str | None = Field(default=None, description="本次流标识")
    trace_id: str | None = Field(default=None, description="请求级追踪标识")
    stage: str = Field(default="reasoning", description="当前阶段")
    message: str = Field(default="模型已开始推理，等待首个可显示 token...", description="用户可见状态")


class PingEvent(ChatEventModel):
    """流式心跳事件。"""

    type: Literal["ping"] = "ping"
    timestamp: str = Field(description="心跳时间")
    trace_id: str | None = Field(default=None, description="请求级追踪标识")
    stage: str = Field(default="reasoning", description="当前阶段")
    message: str = Field(default="模型仍在推理，尚未产出可显示内容...", description="用户可见状态")


class TokenEvent(ChatEventModel):
    """用户可见文本片段事件。"""

    type: Literal["token"] = "token"
    content: str = Field(default="", description="可直接展示给用户的文本")


class ToolStartEvent(ChatEventModel):
    """工具开始事件。"""

    type: Literal["tool_start"] = "tool_start"
    tool: str = Field(description="工具名称")
    input: Any = Field(default_factory=dict, description="已脱敏工具输入")
    run_id: str | None = Field(default=None, description="工具运行标识")
    trace_id: str | None = Field(default=None, description="请求级追踪标识")
    stage: str | None = Field(default=None, description="工具阶段")
    current_stage: str | None = Field(default=None, description="当前工作流阶段")


class ToolEndEvent(ChatEventModel):
    """工具结束事件。"""

    type: Literal["tool_end"] = "tool_end"
    tool: str = Field(description="工具名称")
    result: Any | None = Field(default=None, description="已脱敏工具结果")
    result_preview: str | None = Field(default=None, description="用户可见结果摘要")
    truncated: bool | None = Field(default=None, description="结果是否被截断")
    run_id: str | None = Field(default=None, description="工具运行标识")
    trace_id: str | None = Field(default=None, description="请求级追踪标识")
    stage: str | None = Field(default=None, description="工具阶段")
    current_stage: str | None = Field(default=None, description="当前工作流阶段")
    stage_duration_ms: float | None = Field(default=None, description="阶段耗时")
    evidence: list[dict[str, Any]] = Field(default_factory=list, description="证据摘要")
    evidence_count: int = Field(default=0, description="证据数量")
    evidence_ids: list[str] = Field(default_factory=list, description="证据标识")
    action_guard: dict[str, Any] | None = Field(default=None, description="动作门禁")


class ToolProgressEvent(ChatEventModel):
    """工具结构化进度事件。"""

    type: Literal["tool_progress"] = "tool_progress"
    event_type: Literal["tool_progress"] = "tool_progress"
    trace_id: str = Field(description="请求级追踪标识")
    run_id: str = Field(description="工具运行标识")
    tool_name: str = Field(description="工具名称")
    stage: str = Field(description="工具阶段")
    message: str = Field(description="进度说明")
    progress: float | None = Field(default=None, ge=0.0, le=1.0, description="阶段进度")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加信息")


class ToolStreamEvent(ChatEventModel):
    """工具流式输出扩展事件。"""

    type: Literal["tool_stream"] = "tool_stream"
    event_type: Literal["tool_stream"] = "tool_stream"
    trace_id: str = Field(description="请求级追踪标识")
    run_id: str = Field(description="工具运行标识")
    tool_name: str = Field(description="工具名称")
    chunk: str = Field(default="", description="工具输出片段")
    done: bool = Field(default=False, description="是否已结束")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加信息")


class ChatCompleteEvent(ChatEventModel):
    """聊天完成事件。"""

    type: Literal["chat_complete"] = "chat_complete"
    thread_id: str = Field(description="会话线程")
    trace_id: str | None = Field(default=None, description="请求级追踪标识")
    final_content: str = Field(default="", description="最终展示内容")
    todos: list[Any] = Field(default_factory=list, description="Todo 列表")
    event_count: int = Field(default=0, description="上游事件数量")
    timestamp: str = Field(description="完成时间")


class ServerErrorEvent(ChatEventModel):
    """服务端错误事件。"""

    type: Literal["error"] = "error"
    message: str = Field(default="请求处理失败，请稍后重试", description="用户可见错误")
    error_id: str = Field(description="错误标识")
    trace_id: str = Field(description="请求级追踪标识")
    error: dict[str, Any] = Field(default_factory=dict, description="结构化错误")

