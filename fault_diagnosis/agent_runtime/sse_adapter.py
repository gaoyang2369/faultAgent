"""聊天事件到 SSE 协议帧的适配器。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from ..common.logger import get_logger

_log = get_logger("agent_runtime.sse_adapter")

_TRACE_EVENT_NAMES = {
    "start",
    "ping",
    "tool_start",
    "tool_end",
    "tool_progress",
    "tool_stream",
    "complete",
    "server_error",
}


def build_trace_id(request_id: str) -> str:
    """为 SSE 会话构造稳定 trace 标识。"""

    normalized = "".join(ch for ch in request_id if ch.isalnum())
    return f"trace_{normalized[:16] or 'stream'}"


def event_payload_with_type(payload: dict[str, Any], event_type_key: str = "event_type") -> dict[str, Any]:
    """补齐前端事件通用的 type 字段。"""

    enriched = dict(payload)
    if "type" not in enriched:
        enriched["type"] = enriched.get(event_type_key)
    return enriched


def payload_to_dict(payload: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """将事件模型或普通字典转换为可编码 payload。"""

    if isinstance(payload, BaseModel):
        return payload.model_dump()
    return dict(payload)


def _sanitize_for_json(value: Any) -> Any:
    """将常见运行时对象转换为 JSON 友好结构。"""

    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(key): _sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_json(item) for item in value]
    return value


def _safe_json_dumps(value: Any) -> str:
    """安全序列化 SSE payload。"""

    try:
        return json.dumps(_sanitize_for_json(value), ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps(
            {
                "error": f"serialization_failed: {str(exc)}",
                "original_type": type(value).__name__,
            },
            ensure_ascii=False,
        )


def _summarize_identifier_for_log(value: Any, keep: int = 10) -> str:
    """压缩日志中的长标识。"""

    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= keep * 2:
        return text
    return f"{text[:keep]}...{text[-keep:]}"


def encode_sse_event(
    event_name: str,
    payload: BaseModel | dict[str, Any],
    *,
    trace_id: str | None = None,
) -> str:
    """把事件 payload 编码为标准 SSE 帧。"""

    data = payload_to_dict(payload)
    data = event_payload_with_type(data)
    if trace_id and event_name in _TRACE_EVENT_NAMES and not data.get("trace_id"):
        data["trace_id"] = trace_id
    return f"event: {event_name}\ndata: {_safe_json_dumps(data)}\n\n"


def build_server_error_payload(
    *,
    message: str,
    error_id: str,
    trace_id: str,
    code: str = "INTERNAL_ERROR",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """构造兼容旧前端的结构化错误事件。"""

    error_event = {
        "event_type": "server_error",
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
            "trace_id": trace_id,
            "run_id": run_id,
        },
    }
    payload = event_payload_with_type(error_event)
    payload["type"] = "error"
    payload["message"] = message
    payload["error_id"] = error_id
    payload["trace_id"] = trace_id
    return payload


def parse_sse_chunk(chunk: str) -> tuple[str, dict[str, Any]] | None:
    """解析单个 SSE 帧，解析失败时返回 None。"""

    event_name = "message"
    data_lines: list[str] = []
    for line in chunk.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip() or event_name
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not data_lines:
        return None
    try:
        data = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return event_name, data


def enrich_complete_payload(data: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """为单 Agent complete 事件补充前端结构化诊断字段。"""

    try:
        from ..runtime.diagnosis_contract_adapter import build_diagnosis_contract_payload
        from ..diagnosis.artifact_store import get_thread_artifact

        contract_payload = build_diagnosis_contract_payload(get_thread_artifact(thread_id))
    except Exception as exc:
        _log.warning(
            "诊断结构化契约适配失败",
            thread_id=_summarize_identifier_for_log(thread_id, keep=10),
            error=str(exc),
        )
        return data
    if not contract_payload:
        return data

    enriched = dict(data)
    for key, value in contract_payload.items():
        if key not in enriched or enriched.get(key) in (None, [], {}):
            enriched[key] = value
    return enriched


def adapt_sse_payload(
    event_name: str,
    payload: dict[str, Any],
    *,
    trace_id: str | None,
    thread_id: str,
) -> tuple[str, dict[str, Any]]:
    """对已有 SSE payload 做兼容增强和脱敏补齐。"""

    data = dict(payload)
    if trace_id and event_name in _TRACE_EVENT_NAMES and not data.get("trace_id"):
        data["trace_id"] = trace_id

    if event_name == "complete" and data.get("type") == "chat_complete":
        target_thread_id = str(data.get("thread_id") or thread_id)
        data = enrich_complete_payload(data, target_thread_id)

    if event_name == "server_error" and not isinstance(data.get("error"), dict):
        error_trace_id = str(data.get("trace_id") or trace_id or "trace_unavailable")
        message = str(data.get("message") or "请求处理失败，请稍后重试")
        error_id = str(data.get("error_id") or error_trace_id)
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        error_category = data.get("error_category")
        if error_category and "category" not in details:
            details = {**details, "category": error_category}
        data = build_server_error_payload(
            message=message,
            error_id=error_id,
            trace_id=error_trace_id,
            code=str(data.get("code") or "INTERNAL_ERROR"),
            retryable=bool(data.get("retryable", False)),
            details=details,
        )

    return event_name, data


def adapt_sse_chunk(
    chunk: str,
    trace_id: str | None,
    *,
    thread_id: str,
) -> str:
    """为单 Agent / Dev 路径的既有 SSE 帧补充统一协议字段。"""

    parsed = parse_sse_chunk(chunk)
    if parsed is None:
        return chunk
    event_name, data = parsed
    event_name, data = adapt_sse_payload(
        event_name,
        data,
        trace_id=trace_id,
        thread_id=thread_id,
    )
    return encode_sse_event(event_name, data)


def enrich_diagnosis_sse_chunk(chunk: str, thread_id: str) -> str:
    """兼容增强诊断 SSE 帧，不改变既有事件外壳。"""

    return adapt_sse_chunk(chunk, None, thread_id=thread_id)
