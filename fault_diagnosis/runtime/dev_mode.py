"""本地开发模式辅助模块。

允许应用在无 PostgreSQL/MySQL/Ollama 或外部 API 的情况下启动。
此模式仅用于 UI 和 SSE 工作流验证。
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from datetime import datetime
from types import SimpleNamespace
from typing import Any, AsyncGenerator

from ..common.logger import get_logger
from ..common.utils import safe_json_dumps
from ..common.utils import summarize_identifier_for_log, summarize_text_for_log
from ..common.paths import REPORTS_DIR
from ..security.contracts import AuthContext
from ..security.permissions import build_auth_context
from ..security.policy_engine import authorize_workflow
from ..single_agent.output.payloads import build_ui_payload
from ..single_agent.workflow.policies import build_workflow_plan
from ..single_agent.workflow.router import route_task

_log = get_logger("dev_mode")


def init_dev_state(app) -> None:
    """初始化本地开发模式使用的内存状态。"""
    app.state.dev_mode = True
    app.state.dev_messages = {}
    app.state.dev_todos = {}
    app.state.dev_updated_at = {}


def _timestamp() -> str:
    return datetime.now().isoformat()


def build_dev_todos(message: str, *, report_enabled: bool = False) -> list[dict[str, Any]]:
    """创建用于 UI 验证的确定性待办列表。"""
    summary = message.strip()[:24] or "本地开发模式演示"
    todos = [
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
    ]
    if report_enabled:
        todos.append(
            {
                "id": "dev_todo_4",
                "title": "生成演示报告",
                "description": "写入受保护报告目录，验证报告链接和权限字段",
                "status": "completed",
            }
        )
    return todos


def _reports_dir() -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return REPORTS_DIR


def create_dev_report(
    thread_id: str,
    message: str,
    user_identity: str,
    *,
    auth_context: AuthContext,
    diagnosis_object: str = "",
) -> str:
    """生成用于权限验收的轻量级 HTML 报告。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_thread = "".join(ch for ch in thread_id if ch.isalnum() or ch in ("-", "_")) or "default"
    filename = f"local_dev_report_{sanitized_thread}_{ts}.html"
    file_path = os.path.join(_reports_dir(), filename)
    content = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>本地权限验收报告</title></head>
<body><h1>本地开发模式演示报告</h1>
<p>生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
<p>会话 ID：{html.escape(thread_id)}</p>
<p>用户身份：{html.escape(user_identity)}</p>
<h2>原始请求</h2><p>{html.escape(message)}</p>
<p>当前报告仅用于 LOCAL_DEV_MODE 权限与 SSE 契约验收。</p></body></html>
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    access_payload = {
        "diagnosis_object": diagnosis_object,
        "authorized_asset_scope": list(auth_context.asset_scope),
        "authorized_table_scope": list(auth_context.table_scope),
        "generated_by": auth_context.user_id,
        "auth_method": auth_context.auth_method,
    }
    with open(f"{file_path}.access.json", "w", encoding="utf-8") as handle:
        json.dump(access_payload, handle, ensure_ascii=False, indent=2)
    return filename


def build_dev_response(
    message: str,
    user_identity: str,
    report_filename: str | None,
    *,
    authorization: dict[str, Any],
) -> str:
    """创建本地开发模式下的用户响应文本。"""
    lines = [
            "## 本地开发模式",
            "",
            "当前请求已通过本地开发模式处理。",
            "",
            f"- 用户身份：{user_identity}",
            f"- 原始请求：{message}",
            f"- 授权结果：{authorization.get('mode', 'deny')}",
            "- 已跳过 PostgreSQL / MySQL / Ollama",
            "- 当前重点是验证前端联通、SSE 流式与服务端权限边界",
            "",
            "如需真实故障诊断，请关闭 `LOCAL_DEV_MODE` 并接入真实数据库与外部服务。",
        ]
    if report_filename:
        lines.insert(-2, f"- 演示报告：`{report_filename}`")
    elif authorization.get("mode") in {"deny", "degrade", "clarify"}:
        lines.insert(-2, f"- 权限说明：{authorization.get('user_message') or authorization.get('reason')}")
    return "\n".join(lines)


def build_dev_authorization(
    message: str,
    auth_context: AuthContext,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the local mock through the same router, workflow plan and policy engine."""

    route = route_task(payload={}, message=message)
    plan = build_workflow_plan(route, needs_report=route.requested_output == "report")
    decision = SimpleNamespace(
        primary_task_type=route.primary_task_type.value,
        objects=route.objects.model_dump(exclude_none=True),
        enabled_nodes=plan.resolved_nodes,
        runtime_tools=plan.runtime_tools,
    )
    authorization = authorize_workflow(auth_context, decision).model_dump()
    decision.authorization = authorization
    decision_payload = {
        "primary_task_type": route.primary_task_type.value,
        "objects": route.objects.model_dump(exclude_none=True),
        "requested_output": route.requested_output,
        "risk_level": route.risk_level,
        "enabled_nodes": authorization.get("allowed_nodes", {}),
        "runtime_tools": authorization.get("runtime_tools", []),
        "authorization": authorization,
        "ui_payload": build_ui_payload(decision=decision),
    }
    return decision_payload, authorization


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
    auth_context: AuthContext | None = None,
) -> AsyncGenerator[str, None]:
    """不依赖外部服务，直接发送 SSE 事件流。"""
    _log.info(
        "本地开发模式流式会话开始",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        user_identity=user_identity,
        message_len=len(message),
        message_preview=summarize_text_for_log(message, limit=72),
    )
    trusted_auth = auth_context or build_auth_context(role="guest")
    decision, authorization = build_dev_authorization(message, trusted_auth)
    yield f"event: start\ndata: {safe_json_dumps({'type': 'chat_start', 'thread_id': thread_id})}\n\n"

    runtime_tools = list(authorization.get("runtime_tools", [])) if authorization.get("allowed") else []
    report_enabled = "save_report" in runtime_tools
    todos = build_dev_todos(message, report_enabled=report_enabled)
    if cancel_event is not None and cancel_event.is_set():
        _log.info(
            "本地开发模式在首个工具前收到取消信号",
            thread_id=summarize_identifier_for_log(thread_id, keep=10),
        )
        return

    for tool_name in runtime_tools:
        if tool_name == "save_report":
            continue
        tool_input = {"query": message}
        yield (
            "event: tool_start\ndata: "
            f"{safe_json_dumps({'type': 'tool_start', 'tool': tool_name, 'input': tool_input})}\n\n"
        )
        yield (
            "event: tool_end\ndata: "
            f"{safe_json_dumps({'type': 'tool_end', 'tool': tool_name, 'result': {'mocked': True, 'authorized': True}})}\n\n"
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

    report_filename: str | None = None
    if report_enabled:
        requested_assets = decision.get("objects", {}).get("device_ids", [])
        report_filename = create_dev_report(
            thread_id,
            message,
            user_identity,
            auth_context=trusted_auth,
            diagnosis_object=str(requested_assets[0]) if requested_assets else "",
        )
        yield (
            "event: tool_start\ndata: "
            f"{safe_json_dumps({'type': 'tool_start', 'tool': 'save_report', 'input': {'report_filename': report_filename}})}\n\n"
        )
        yield (
            "event: tool_end\ndata: "
            f"{safe_json_dumps({'type': 'tool_end', 'tool': 'save_report', 'result': f'报告已保存至：{report_filename}'})}\n\n"
        )

    final_content = build_dev_response(
        message,
        user_identity,
        report_filename,
        authorization=authorization,
    )
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
        "decision": decision,
        "authorization": authorization,
        "ui_payload": decision.get("ui_payload"),
        "report_filename": report_filename,
        "report_url": f"/reports/{report_filename}" if report_filename else None,
        "permission_check": {
            "allowed": False,
            "decision": "deny_direct_execution",
            "reason": "所有角色均不得直接执行设备控制动作。",
        }
        if decision["primary_task_type"] == "action_request"
        else {},
        "todos": todos,
        "event_count": len(final_content) + 2 + len(runtime_tools) * 2,
        "timestamp": _timestamp(),
    }
    _log.info(
        "本地开发模式流式请求完成",
        thread_id=summarize_identifier_for_log(thread_id, keep=10),
        token_count=len(final_content),
        tool_event_count=len(runtime_tools) * 2,
        todo_count=len(todos),
    )
    yield f"event: complete\ndata: {safe_json_dumps(completion_data)}\n\n"
