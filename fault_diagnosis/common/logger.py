"""Structured logging helpers for console and file output."""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from .encoding import ensure_utf8_stdio
from .paths import RUN_STATE_DIR


ensure_utf8_stdio()

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    """Generate and bind a new request id for the current context."""
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def bind_request_id(request_id: str) -> str:
    """Bind an existing request id to the current context."""
    rid = str(request_id or "").strip()
    if rid:
        _request_id.set(rid)
    return rid


def ensure_request_id() -> str:
    """Return the current request id or create one if the context is empty."""
    rid = get_request_id()
    if rid:
        return rid
    return new_request_id()


def get_request_id() -> str:
    return _request_id.get("")


def _build_payload(record: logging.LogRecord, *, include_exception: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "module": record.name,
        "msg": record.getMessage(),
    }

    rid = get_request_id()
    if rid:
        payload["request_id"] = rid

    if record.levelno >= logging.WARNING:
        payload["loc"] = f"{record.filename}:{record.lineno}"

    skip = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "taskName",
    }
    for key, value in record.__dict__.items():
        if key not in skip:
            payload[key] = value

    if include_exception and record.exc_info:
        payload["exc"] = logging.Formatter().formatException(record.exc_info)

    return payload


class _JsonFileFormatter(logging.Formatter):
    """Single-line JSON formatter for file output."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            _build_payload(record, include_exception=True),
            ensure_ascii=False,
            default=str,
        )


class _ConsoleFormatter(logging.Formatter):
    """Compact console formatter that avoids printing full tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        payload = _build_payload(record, include_exception=False)
        fields = [
            payload.get("ts", ""),
            payload.get("level", ""),
            payload.get("module", ""),
            payload.get("msg", ""),
        ]

        extras: list[str] = []
        for key in (
            "request_id",
            "trace_id",
            "error_id",
            "method",
            "path",
            "status_code",
            "status",
            "duration_ms",
            "client",
            "stage",
            "operation",
            "tool",
            "run_id",
            "thread_id",
            "stream_id",
            "round",
            "tool_call_count",
            "prompt_chars",
            "output_chars",
            "input_preview",
            "result_preview",
            "summary",
            "decision",
            "error",
            "error_category",
        ):
            value = payload.get(key)
            if value not in (None, "", []):
                extras.append(f"{key}={value}")

        if extras:
            fields.append(" | ".join(extras))
        return " | ".join(str(item) for item in fields if item)


def _log_file_path() -> str:
    os.makedirs(RUN_STATE_DIR, exist_ok=True)
    return os.path.join(RUN_STATE_DIR, "app-json.log")


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_ConsoleFormatter())
    console_handler.setLevel(logging.INFO)

    file_handler = logging.FileHandler(_log_file_path(), encoding="utf-8")
    file_handler.setFormatter(_JsonFileFormatter())
    file_handler.setLevel(logging.DEBUG)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


class JsonLoggerAdapter(logging.LoggerAdapter):
    """Move non-standard kwargs into logging.extra automatically."""

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        keys_to_move = [key for key in kwargs if key not in ("exc_info", "stack_info", "extra")]
        for key in keys_to_move:
            extra[key] = kwargs.pop(key)
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str = "app") -> logging.LoggerAdapter:
    """Return a structured logger adapter."""
    logger = _build_logger(name)
    return JsonLoggerAdapter(logger, {})


logger = get_logger("app")
