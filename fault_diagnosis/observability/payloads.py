"""Payload sanitization helpers for trace export."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, date
from typing import Any

try:  # pragma: no cover - optional dependency shape
    from pydantic import BaseModel
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore[assignment]

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|pwd|token|authorization|cookie|mysql_pw|postgres_password|session_secret)",
    re.IGNORECASE,
)
_URI_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/?#@]+)(:(?P<pw>[^@/?#]*))?@",
    re.IGNORECASE,
)
_GENERIC_TOKEN_RE = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{12,}|pk-[A-Za-z0-9_-]{12,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{10,})\b"
)


def _looks_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key or "")))


def _short_hash(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:12]


def _truncate_text(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}...(truncated)"


def _redact_string(text: str) -> str:
    redacted = _URI_CREDENTIAL_RE.sub(lambda match: f"{match.group('scheme')}{match.group('user')}:REDACTED@", text)
    redacted = _GENERIC_TOKEN_RE.sub("[REDACTED]", redacted)
    return redacted


def sanitize_trace_value(
    value: Any,
    *,
    capture_content: bool,
    preview_chars: int,
    max_depth: int = 6,
    max_items: int = 20,
    _depth: int = 0,
) -> Any:
    """Convert arbitrary runtime payloads into a safe trace-friendly shape."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(exclude_none=True)
        except TypeError:
            value = value.model_dump()

    if isinstance(value, str):
        if not capture_content:
            return {
                "captured": False,
                "type": "str",
                "chars": len(value),
                "sha256_12": _short_hash(value),
            }
        return _truncate_text(_redact_string(value), preview_chars)

    if _depth >= max_depth:
        return {"type": type(value).__name__, "truncated": True}

    if isinstance(value, dict):
        if not capture_content:
            keys = [str(key) for key in list(value.keys())[:max_items]]
            return {
                "captured": False,
                "type": "dict",
                "keys": keys,
                "size": len(value),
            }
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                sanitized["__truncated__"] = f"+{len(value) - max_items} items"
                break
            key_text = str(key)
            if _looks_sensitive_key(key_text):
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = sanitize_trace_value(
                    item,
                    capture_content=capture_content,
                    preview_chars=preview_chars,
                    max_depth=max_depth,
                    max_items=max_items,
                    _depth=_depth + 1,
                )
        return sanitized

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if not capture_content:
            return {
                "captured": False,
                "type": type(value).__name__,
                "size": len(items),
            }
        sanitized_list = [
            sanitize_trace_value(
                item,
                capture_content=capture_content,
                preview_chars=preview_chars,
                max_depth=max_depth,
                max_items=max_items,
                _depth=_depth + 1,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            sanitized_list.append(f"...(+{len(items) - max_items} items)")
        return sanitized_list

    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        rendered = str(value)
    if not capture_content:
        return {
            "captured": False,
            "type": type(value).__name__,
            "chars": len(rendered),
            "sha256_12": _short_hash(rendered),
        }
    return _truncate_text(_redact_string(rendered), preview_chars)

