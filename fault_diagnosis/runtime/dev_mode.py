"""本地开发模式辅助模块。

允许应用在无 PostgreSQL/MySQL/Ollama 或外部 API 的情况下启动。
此模式仅用于 UI 和 SSE 工作流验证。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, AsyncGenerator

from ..common.logger import get_logger
from ..common.utils import safe_json_dumps
from ..common.utils import summarize_identifier_for_log, summarize_text_for_log
from ..common.paths import REPORTS_DIR

_log = get_logger("dev_mode")


def init_dev_state(app) -> None:
    """初始化本地开发模式使用的内存状态。"""
    app.state.dev_mode = True
    app.state.dev_messages = {}
    app.state.dev_todos = {}
    app.state.dev_updated_at = {}


def _timestamp() -> str:
    return datetime.now().isoformat()


def build_dev_todos(message: str) -> list[dict[str, Any]]:
    """创建用于 UI 验证的确定性待办列表。"""
    summary = message.strip()[:24] or "本地开发模式演示"
    return [
        {
            "id": "dev_todo_1",
            "title": "接收请求",
            "description": f"已接收：{summary}",
            "status": "completed",
        },
        {
            "id": "dev_todo_2",
            "title": "跳过外部依赖",
            "description": "已跳过 PostgreSQL / MySQL / Ollama / 外部诊断 API",
            "status": "completed",
        },
        {
            "id": "dev_todo_3",
            "title": "生成演示回复",
            "description": "使用内存会话和模拟 SSE 验证前后端联通",
            "status": "completed",
        },
        {
            "id": "dev_todo_4",
            "title": "生成演示报告",
            "description": "写入 public/reports，验证报告链接和静态文件挂载",
            "status": "completed",
        },
    ]


def _reports_dir() -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return REPORTS_DIR


def create_dev_report(thread_id: str, message: str, user_identity: str) -> str:
    """生成用于 UI 验证的轻量级 Markdown 报告。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_thread = "".join(ch for ch in thread_id if ch.isalnum() or ch in ("-", "_")) or "default"
    filename = f"local_dev_report_{sanitized_thread}_{ts}.md"
    file_path = os.path.join(_reports_dir(), filename)
    content = f"""# 本地开发模式演示报告

**生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**会话 ID**：{thread_id}
**用户身份**：{user_identity}

## 说明

- 当前运行在 `LOCAL_DEV_MODE`
- 已跳过 PostgreSQL / MySQL / Ollama / Tavily / 外部故障诊断 API
- 当前报告用于验证：SSE、消息渲染、报告链接、静态文件服务

## 原始请求

{message}

## 下一步

如需切回真实诊断链路，请关闭 `LOCAL_DEV_MODE` 并提供可用的 PostgreSQL / MySQL / 外部服务连接。
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return filename


def build_dev_response(message: str, user_identity: str, report_filename: str) -> str:
    """创建本地开发模式下的用户响应文本。"""
    return "\n".join(
        [
            "## 本地开发模式",
            "",
            "当前请求已通过本地开发模式处理。",
            "",
            f"- 用户身份：{user_identity}",
            f"- 原始请求：{message}",
            "- 已跳过 PostgreSQL / MySQL / Ollama / Tavily / 外部故障诊断 API",
            "- 当前重点是验证前端联通、SSE 流式、历史记录接口、任务面板和报告落盘",
            f"- 演示报告：`{report_filename}`",
            "",
            "如需真实故障诊断，请关闭 `LOCAL_DEV_MODE` 并接入真实数据库与外部服务。",
        ]
    )


def _summarize_todos(todos: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(todos),
        "pending": sum(1 for item in todos if item.get("status") == "pending"),
        "in_progress": sum(1 for item in todos if item.get("status") == "in_progress"),
        "completed": sum(1 for item in todos if item.get("status") == "completed"),
    }


def record_dev_exchange(
    app,
    thread_id: str,
    user_message: str,
    assistant_message: str,
    todos: list[dict[str, Any]],
    *,
    stream_state: str = "completed",
    status_text: str = "",
) -> None:
    """将本地开发模式的消息和待办事项持久化到内存中。"""
    timestamp = _timestamp()
    messages = app.state.dev_messages.setdefault(thread_id, [])
    messages.append({"role": "user", "content": user_message, "timestamp": timestamp})
    messages.append(
        {
            "role": "assistant",
            "content": assistant_message,
            "timestamp": timestamp,
            "isMarkdown": True,
            "streamState": stream_state,
            "statusText": status_text,
        }
    )
    app.state.dev_todos[thread_id] = todos
    app.state.dev_updated_at[thread_id] = timestamp


def list_dev_threads(app) -> list[str]:
    """返回按最近更新时间降序排列的会话 ID 列表。"""
    items = sorted(
        app.state.dev_updated_at.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    return [thread_id for thread_id, _ in items]


def get_dev_messages(app, thread_id: str) -> list[dict[str, Any]]:
    return app.state.dev_messages.get(thread_id, [])


def get_dev_todos_payload(app, thread_id: str) -> dict[str, Any]:
    todos = app.state.dev_todos.get(thread_id, [])
    return {
        "thread_id": thread_id,
        "todos": todos,
        "summary": _summarize_todos(todos),
    }


async def stream_dev_chat_events(
    app,
    message: str,
    thread_id: str,
    user_identity: str,
    cancel_event: asyncio.Event | None = None,
) -> AsyncGenerator[str, None]:
    """不依赖外部服务，直接发送 SSE 事件流。"""
    _log.info(
        "本地开发模式流式会话开始",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        user_identity=user_identity,
        message_len=len(message),
        message_preview=summarize_text_for_log(message, limit=72),
    )
    yield f"event: start\ndata: {safe_json_dumps({'type': 'chat_start', 'thread_id': thread_id})}\n\n"

    todos = build_dev_todos(message)
    if cancel_event is not None and cancel_event.is_set():
        _log.info(
            "本地开发模式在首个工具前收到取消信号",
            thread_id=summarize_identifier_for_log(thread_id, keep=10),
        )
        return

    _log.info(
        "本地开发模式工具开始",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        tool_name="write_todos",
    )
    yield (
        "event: tool_start\ndata: "
        f"{safe_json_dumps({'type': 'tool_start', 'tool': 'write_todos', 'input': {'thread_id': thread_id}})}\n\n"
    )
    yield (
        "event: tool_end\ndata: "
        f"{safe_json_dumps({'type': 'tool_end', 'tool': 'write_todos', 'result': {'todos': todos}})}\n\n"
    )
    _log.info(
        "本地开发模式工具完成",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        tool_name="write_todos",
        todo_count=len(todos),
    )

    if cancel_event is not None and cancel_event.is_set():
        _log.info(
            "本地开发模式在 write_todos 后收到取消信号",
            thread_id=summarize_identifier_for_log(thread_id, keep=10),
        )
        record_dev_exchange(
            app,
            thread_id,
            message,
            "",
            todos,
            stream_state="interrupted",
            status_text="已停止生成",
        )
        return

    report_filename = create_dev_report(thread_id, message, user_identity)
    _log.info(
        "本地开发模式工具开始",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        tool_name="save_report",
    )
    yield (
        "event: tool_start\ndata: "
        f"{safe_json_dumps({'type': 'tool_start', 'tool': 'save_report', 'input': {'report_filename': report_filename}})}\n\n"
    )
    yield (
        "event: tool_end\ndata: "
        f"{safe_json_dumps({'type': 'tool_end', 'tool': 'save_report', 'result': f'报告已保存至：{report_filename}'})}\n\n"
    )
    _log.info(
        "本地开发模式工具完成",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        tool_name="save_report",
        report_filename=report_filename,
    )

    final_content = build_dev_response(message, user_identity, report_filename)
    emitted_content = ""
    for char in final_content:
        if cancel_event is not None and cancel_event.is_set():
            _log.info(
                "本地开发模式流式输出收到取消信号",
                thread_id=summarize_identifier_for_log(thread_id, keep=10),
                token_count=len(emitted_content),
            )
            record_dev_exchange(
                app,
                thread_id,
                message,
                emitted_content,
                todos,
                stream_state="interrupted",
                status_text="已停止生成",
            )
            return
        emitted_content += char
        yield f"event: token\ndata: {safe_json_dumps({'type': 'token', 'content': char})}\n\n"
        await asyncio.sleep(0.002)

    record_dev_exchange(app, thread_id, message, final_content, todos)
    completion_data = {
        "type": "chat_complete",
        "thread_id": thread_id,
        "final_content": final_content,
        "todos": todos,
        "event_count": len(final_content) + 5,
        "timestamp": _timestamp(),
    }
    _log.info(
        "本地开发模式流式请求完成",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        token_count=len(final_content),
        tool_event_count=4,
        todo_count=len(todos),
    )
    yield f"event: complete\ndata: {safe_json_dumps(completion_data)}\n\n"
