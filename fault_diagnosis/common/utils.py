"""Generic utility functions for JSON serialization and todo parsing."""
import json
import ast
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

_MESSAGE_ROLE_ALIASES = {
    "human": "user",
    "humanmessage": "user",
    "user": "user",
    "ai": "assistant",
    "aimessage": "assistant",
    "assistant": "assistant",
    "tool": "tool",
    "toolmessage": "tool",
    "system": "system",
    "systemmessage": "system",
}


def _normalize_message_role(value: Any) -> str:
    if value is None:
        return "assistant"

    text = str(value).strip()
    if not text:
        return "assistant"

    return _MESSAGE_ROLE_ALIASES.get(text.lower(), text)


def _normalize_message_content(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, (list, tuple)):
        parts = [_normalize_message_content(item).strip() for item in value]
        return "\n".join(part for part in parts if part)

    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("text"), dict):
            nested = value["text"]
            return _normalize_message_content(
                nested.get("value") or nested.get("text") or nested.get("content")
            )
        if isinstance(value.get("value"), str):
            return value["value"]
        content = value.get("content")
        if isinstance(content, (str, list, tuple)):
            return _normalize_message_content(content)
        if isinstance(value.get("output_text"), str):
            return value["output_text"]
        if isinstance(value.get("input_text"), str):
            return value["input_text"]
        if value.get("type") == "image_url" or value.get("image_url"):
            return "[图片]"

    try:
        return str(value)
    except Exception:
        return "<unserializable content>"


# Helper: convert LangChain objects into JSON-serializable structures.
def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively normalize objects into JSON-safe values.
    """
    # None
    if obj is None:
        return None

    # LangChain messages
    if isinstance(obj, (HumanMessage, AIMessage, ToolMessage)):
        try:
            raw_role = getattr(obj, "type", None) or getattr(obj, "role", None) or obj.__class__.__name__
            serialized = {
                "type": obj.__class__.__name__,
                "content": _normalize_message_content(obj.content if hasattr(obj, "content") else str(obj)),
                "role": _normalize_message_role(raw_role),
            }
            raw_name = getattr(obj, "name", None)
            if isinstance(raw_name, str) and raw_name.strip():
                serialized["name"] = raw_name.strip()
            raw_tool_call_id = getattr(obj, "tool_call_id", None)
            if raw_tool_call_id:
                serialized["tool_call_id"] = str(raw_tool_call_id)
            return serialized
        except Exception:
            return str(obj)

    # Dict
    if isinstance(obj, dict):
        try:
            return {key: sanitize_for_json(value) for key, value in obj.items()}
        except Exception:
            return str(obj)

    # List / tuple
    if isinstance(obj, (list, tuple)):
        try:
            return [sanitize_for_json(item) for item in obj]
        except Exception:
            return str(obj)

    # Primitive values
    if isinstance(obj, (str, int, float, bool)):
        return obj

    # Datetime
    if isinstance(obj, datetime):
        return obj.isoformat()

    # Other serializable values
    try:
        json.dumps(obj, default=str)
        return obj
    except (TypeError, ValueError):
        try:
            return str(obj)
        except Exception:
            return f"<unserializable object: {type(obj).__name__}>"


def sanitize_chat_history_messages(obj: Any) -> Any:
    """为 history 接口清理消息，避免前端渲染空 assistant 气泡。"""
    sanitized = sanitize_for_json(obj)
    if not isinstance(sanitized, list):
        return sanitized

    cleaned_messages = []
    for message in sanitized:
        if not isinstance(message, dict):
            cleaned_messages.append(message)
            continue

        role = _normalize_message_role(message.get("role") or message.get("type"))
        content = _normalize_message_content(message.get("content"))
        normalized_message = {
            **message,
            "role": role,
            "content": content,
        }

        if role == "assistant" and not content.strip():
            continue
        cleaned_messages.append(normalized_message)
    return cleaned_messages


def summarize_identifier_for_log(value: Any, keep: int = 10) -> str:
    """压缩 thread_id / session_id 等长标识，避免日志中出现完整敏感值。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= keep * 2:
        return text
    return f"{text[:keep]}...{text[-keep:]}"


def summarize_text_for_log(value: Any, limit: int = 96) -> str:
    """压缩文本日志，保留可读摘要，避免打印完整用户输入。"""
    text = _normalize_message_content(value)
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def summarize_value_for_log(value: Any, limit: int = 160) -> str:
    """将复杂对象压成简短摘要，适合工具输入/输出日志。"""
    sanitized = sanitize_for_json(value)
    if isinstance(sanitized, (dict, list)):
        try:
            text = json.dumps(sanitized, ensure_ascii=False, default=str)
        except Exception:
            text = str(sanitized)
    else:
        text = _normalize_message_content(sanitized)
    return summarize_text_for_log(text, limit=limit)


# Safe JSON serialization wrapper.
def safe_json_dumps(obj: Any, ensure_ascii: bool = False) -> str:
    """
    Serialize an object to JSON after sanitizing unsupported values.
    """
    try:
        cleaned_obj = sanitize_for_json(obj)
        return json.dumps(cleaned_obj, ensure_ascii=ensure_ascii, default=str)
    except Exception as e:
        return json.dumps({"error": f"serialization_failed: {str(e)}", "original_type": type(obj).__name__}, ensure_ascii=ensure_ascii)


# ===== Todo parsing helpers =====

def _normalize_status(value: Any) -> str:
    if not value:
        return "pending"
    text = str(value).strip().lower()
    mapping = {
        "pending": "pending",
        "todo": "pending",
        "not_started": "pending",
        "not-started": "pending",
        "wait": "pending",
        "in-progress": "in_progress",
        "in_progress": "in_progress",
        "doing": "in_progress",
        "working": "in_progress",
        "running": "in_progress",
        "active": "in_progress",
        "completed": "completed",
        "complete": "completed",
        "done": "completed",
        "finished": "completed"
    }
    return mapping.get(text, "completed" if "complete" in text or "done" in text else ("in_progress" if "progress" in text else "pending"))


def _extract_bracket_content(text: str, marker: str) -> Optional[str]:
    """Extract the first balanced bracket block after a marker."""
    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("[", idx)
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        char = text[i]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _normalize_todo_items(raw_todos: List[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for idx, item in enumerate(raw_todos):
        if isinstance(item, dict):
            # Handle LangChain write_todos output shape.
            normalized.append({
                "id": str(item.get("id") or f"todo_{idx}"),
                "title": item.get("content") or item.get("title") or item.get("task") or item.get("description") or f"Task {idx + 1}",
                "description": item.get("description") or item.get("detail") or item.get("notes") or "",
                "status": _normalize_status(item.get("status")),
            })
        else:
            normalized.append({
                "id": f"todo_{idx}",
                "title": str(item),
                "description": "",
                "status": "pending"
            })
    return normalized


def _extract_todo_list_from_output(output: Any):
    if output is None:
        return None

    # Handle Command objects returned by LangChain write_todos.
    if hasattr(output, 'update') and isinstance(output.update, dict):
        if 'todos' in output.update and isinstance(output.update['todos'], list):
            return output.update['todos']

    if isinstance(output, dict):
        if "todos" in output and isinstance(output["todos"], list):
            return output["todos"]
        if "content" in output:
            nested = _extract_todo_list_from_output(output["content"])
            if nested:
                return nested
        return None

    if isinstance(output, list):
        return output

    if isinstance(output, str):
        text = output.strip()
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
                nested = _extract_todo_list_from_output(parsed)
                if nested:
                    return nested
            except Exception:
                pass
        bracket_snippet = None
        match = re.search(r'["\']?todos["\']?\s*:\s*(\[[\s\S]*\])', text)
        if match:
            bracket_snippet = match.group(1)
        else:
            for marker in ("'todos'", '"todos"'):
                bracket_snippet = _extract_bracket_content(text, marker)
                if bracket_snippet:
                    break
        if bracket_snippet:
            for parser in (json.loads, ast.literal_eval):
                try:
                    parsed = parser(bracket_snippet)
                    nested = _extract_todo_list_from_output(parsed)
                    if nested:
                        return nested
                except Exception:
                    pass
    return None


def parse_todos_from_tool_output(output: Any):
    raw_todos = _extract_todo_list_from_output(output)
    if not raw_todos:
        return None
    return _normalize_todo_items(raw_todos)
