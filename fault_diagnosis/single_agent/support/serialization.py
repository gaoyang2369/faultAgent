"""JSON-safe serialization helpers for single-agent events and trace payloads."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def sanitize_for_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return sanitize_for_json(value.model_dump())
    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(item) for item in value]
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(sanitize_for_json(value), ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


def preview(value: Any, limit: int = 800) -> str:
    text = stringify(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"
