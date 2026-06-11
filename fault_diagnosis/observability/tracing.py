"""Optional trace export bridge for the restricted single-agent runtime."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterator, Protocol

from ..common.logger import get_logger
from ..config import (
    AGENT_TRACE_BACKEND,
    AGENT_TRACE_CAPTURE_CONTENT,
    AGENT_TRACE_FLUSH_ON_RUN,
    AGENT_TRACE_FLUSH_TIMEOUT_SECONDS,
    AGENT_TRACE_LOCAL_LOG,
    AGENT_TRACE_LOCAL_LOG_PATH,
    AGENT_TRACE_PREVIEW_CHARS,
    APP_ENV,
)
from .payloads import sanitize_trace_value

_log = get_logger("observability.trace")
_TRACE_EXPORTER_LOCK = RLock()
_TRACE_EXPORTER: "TraceExporter | None" = None


@dataclass(slots=True)
class TraceRunContext:
    """Metadata used to attach a runtime run to an external trace backend."""

    trace_id: str
    request_id: str
    thread_id: str
    user_identity: str
    user_message: str
    stream_id: str = ""
    runtime: str = "restricted_single_agent"
    model_name: str | None = None

    @property
    def langfuse_trace_id(self) -> str:
        seed = self.trace_id or self.request_id or self.thread_id or "restricted_single_agent"
        return _normalize_trace_id(seed)


class TraceObservationHandle(Protocol):
    """Minimal observation interface used by the single-agent runtime."""

    def update(
        self,
        *,
        name: str | None = None,
        input: Any | None = None,
        output: Any | None = None,
        metadata: Any | None = None,
        version: str | None = None,
        level: str | None = None,
        status_message: str | None = None,
        completion_start_time: Any | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        prompt: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        ...

    def end(self, *, end_time: int | None = None) -> Any:
        ...

    def finish(
        self,
        *,
        status: str,
        output: Any | None = None,
        error: str | None = None,
        metadata: Any | None = None,
    ) -> Any:
        ...


class TraceRunHandle(Protocol):
    """Runtime handle for the request-scoped root trace."""

    enabled: bool

    def start_observation(
        self,
        *,
        name: str,
        as_type: str = "span",
        input: Any | None = None,
        output: Any | None = None,
        metadata: Any | None = None,
        version: str | None = None,
        level: str | None = None,
        status_message: str | None = None,
        completion_start_time: Any | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        prompt: Any | None = None,
    ) -> TraceObservationHandle:
        ...

    def observation(
        self,
        *,
        name: str,
        as_type: str = "span",
        input: Any | None = None,
        output: Any | None = None,
        metadata: Any | None = None,
        version: str | None = None,
        level: str | None = None,
        status_message: str | None = None,
        completion_start_time: Any | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        prompt: Any | None = None,
    ) -> Iterator[TraceObservationHandle]:
        ...

    def finish(
        self,
        *,
        status: str,
        output: Any | None = None,
        error: str | None = None,
        metadata: Any | None = None,
    ) -> None:
        ...

    def flush(self) -> None:
        ...

    def close(self) -> None:
        ...


class NoopTraceObservation:
    """No-op observation used when export is disabled or unavailable."""

    def update(self, **_: Any) -> "NoopTraceObservation":
        return self

    def end(self, *, end_time: int | None = None) -> "NoopTraceObservation":  # noqa: ARG002
        return self

    def finish(
        self,
        *,
        status: str,  # noqa: ARG002
        output: Any | None = None,  # noqa: ARG002
        error: str | None = None,  # noqa: ARG002
        metadata: Any | None = None,  # noqa: ARG002
    ) -> "NoopTraceObservation":
        return self


class NoopTraceRun:
    """No-op trace handle for local or unconfigured runs."""

    enabled = False

    def __init__(self, *, trace_context: TraceRunContext | None = None) -> None:
        self.trace_context = trace_context

    def start_observation(
        self,
        *,
        name: str,
        as_type: str = "span",  # noqa: ARG002
        input: Any | None = None,  # noqa: ARG002
        output: Any | None = None,  # noqa: ARG002
        metadata: Any | None = None,  # noqa: ARG002
        version: str | None = None,  # noqa: ARG002
        level: str | None = None,  # noqa: ARG002
        status_message: str | None = None,  # noqa: ARG002
        completion_start_time: Any | None = None,  # noqa: ARG002
        model: str | None = None,  # noqa: ARG002
        model_parameters: dict[str, Any] | None = None,  # noqa: ARG002
        usage_details: dict[str, int] | None = None,  # noqa: ARG002
        cost_details: dict[str, float] | None = None,  # noqa: ARG002
        prompt: Any | None = None,  # noqa: ARG002
    ) -> NoopTraceObservation:
        return NoopTraceObservation()

    @contextmanager
    def observation(self, **kwargs: Any) -> Iterator[TraceObservationHandle]:
        observation = self.start_observation(**kwargs)
        try:
            yield observation
        finally:
            observation.end()

    def finish(
        self,
        *,
        status: str,  # noqa: ARG002
        output: Any | None = None,  # noqa: ARG002
        error: str | None = None,  # noqa: ARG002
        metadata: Any | None = None,  # noqa: ARG002
    ) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def _normalize_trace_id(seed: str) -> str:
    raw = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return raw[:32]


def _build_langfuse_client():
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    host = os.getenv("LANGFUSE_HOST", "").strip()
    base_url = os.getenv("LANGFUSE_BASE_URL", "").strip()

    if not (public_key and secret_key):
        raise RuntimeError("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 未配置")

    from langfuse import Langfuse

    kwargs: dict[str, Any] = {
        "public_key": public_key,
        "secret_key": secret_key,
        "environment": APP_ENV,
    }
    if base_url:
        kwargs["base_url"] = base_url
    elif host:
        kwargs["host"] = host
    return Langfuse(**kwargs)


class LangfuseTraceObservation:
    """Thin wrapper around a Langfuse observation handle."""

    def __init__(self, scope: Any, observation: Any, *, capture_content: bool, preview_chars: int) -> None:
        self._scope = scope
        self._observation = observation
        self._capture_content = capture_content
        self._preview_chars = preview_chars
        self._finished = False

    def update(
        self,
        *,
        name: str | None = None,
        input: Any | None = None,
        output: Any | None = None,
        metadata: Any | None = None,
        version: str | None = None,
        level: str | None = None,
        status_message: str | None = None,
        completion_start_time: Any | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        prompt: Any | None = None,
        **kwargs: Any,
    ) -> "LangfuseTraceObservation":
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if input is not None:
            payload["input"] = sanitize_trace_value(
                input,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
        if output is not None:
            payload["output"] = sanitize_trace_value(
                output,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
        if metadata is not None:
            payload["metadata"] = sanitize_trace_value(
                metadata,
                capture_content=True,
                preview_chars=self._preview_chars,
            )
        if version is not None:
            payload["version"] = version
        if level is not None:
            payload["level"] = level
        if status_message is not None:
            payload["status_message"] = status_message[:500]
        if completion_start_time is not None:
            payload["completion_start_time"] = completion_start_time
        if model is not None:
            payload["model"] = model
        if model_parameters is not None:
            payload["model_parameters"] = sanitize_trace_value(
                model_parameters,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
        if usage_details is not None:
            payload["usage_details"] = usage_details
        if cost_details is not None:
            payload["cost_details"] = cost_details
        if prompt is not None:
            payload["prompt"] = sanitize_trace_value(
                prompt,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
        payload.update(kwargs)
        if payload:
            self._observation.update(**payload)
        return self

    def finish(
        self,
        *,
        status: str,
        output: Any | None = None,
        error: str | None = None,
        metadata: Any | None = None,
    ) -> "LangfuseTraceObservation":
        if self._finished:
            return self
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = sanitize_trace_value(
                output,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
        if metadata is not None:
            payload["metadata"] = sanitize_trace_value(
                metadata,
                capture_content=True,
                preview_chars=self._preview_chars,
            )
        if error is not None:
            payload["level"] = "ERROR"
            payload["status_message"] = _normalize_status_message(error)
        else:
            payload["status_message"] = _normalize_status_message(status)
            if status in {"skipped"}:
                payload["level"] = "DEBUG"
            elif status in {"cancelled"}:
                payload["level"] = "WARNING"
        if payload:
            try:
                self._observation.update(**payload)
            except Exception as exc:  # pragma: no cover - best effort
                _log.warning("Langfuse observation update failed", error=str(exc))
        try:
            self._observation.end()
        except Exception as exc:  # pragma: no cover - best effort
            _log.warning("Langfuse observation end failed", error=str(exc))
        with suppress(Exception):
            self._scope.__exit__(None, None, None)
        self._finished = True
        return self


def _normalize_status_message(message: str) -> str:
    return str(message or "").strip()[:500]


class LangfuseTraceRun:
    """Request-scoped Langfuse trace wrapper."""

    enabled = True

    def __init__(
        self,
        *,
        client: Any,
        scope: Any,
        observation: Any,
        attrs_scope: Any,
        trace_context: TraceRunContext,
        capture_content: bool,
        preview_chars: int,
        flush_on_run: bool,
    ) -> None:
        self._client = client
        self._scope = scope
        self._observation = observation
        self._attrs_scope = attrs_scope
        self.trace_context = trace_context
        self._capture_content = capture_content
        self._preview_chars = preview_chars
        self._flush_on_run = flush_on_run
        self._finished = False

    def start_observation(
        self,
        *,
        name: str,
        as_type: str = "span",
        input: Any | None = None,
        output: Any | None = None,
        metadata: Any | None = None,
        version: str | None = None,
        level: str | None = None,
        status_message: str | None = None,
        completion_start_time: Any | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        prompt: Any | None = None,
    ) -> LangfuseTraceObservation:
        scope = self._client.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=sanitize_trace_value(
                input,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
            if input is not None
            else None,
            output=sanitize_trace_value(
                output,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
            if output is not None
            else None,
            metadata=sanitize_trace_value(
                metadata,
                capture_content=True,
                preview_chars=self._preview_chars,
            )
            if metadata is not None
            else None,
            version=version,
            level=level,
            status_message=status_message,
            completion_start_time=completion_start_time,
            model=model,
            model_parameters=model_parameters,
            usage_details=usage_details,
            cost_details=cost_details,
            prompt=prompt,
            end_on_exit=False,
            trace_context={"trace_id": self.trace_context.langfuse_trace_id},
        )
        observation = scope.__enter__()
        return LangfuseTraceObservation(
            scope,
            observation,
            capture_content=self._capture_content,
            preview_chars=self._preview_chars,
        )

    @contextmanager
    def observation(self, **kwargs: Any) -> Iterator[TraceObservationHandle]:
        observation = self.start_observation(**kwargs)
        try:
            yield observation
        except Exception as exc:
            observation.finish(status="error", error=str(exc))
            raise
        else:
            observation.finish(status="completed")

    def finish(
        self,
        *,
        status: str,
        output: Any | None = None,
        error: str | None = None,
        metadata: Any | None = None,
    ) -> None:
        if self._finished:
            return
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = sanitize_trace_value(
                output,
                capture_content=self._capture_content,
                preview_chars=self._preview_chars,
            )
        if metadata is not None:
            payload["metadata"] = sanitize_trace_value(
                metadata,
                capture_content=True,
                preview_chars=self._preview_chars,
            )
        if error is not None:
            payload["level"] = "ERROR"
            payload["status_message"] = _normalize_status_message(error)
        else:
            payload["status_message"] = _normalize_status_message(status)
            if status in {"skipped"}:
                payload["level"] = "DEBUG"
            elif status in {"cancelled"}:
                payload["level"] = "WARNING"
        if payload:
            try:
                self._observation.update(**payload)
            except Exception as exc:  # pragma: no cover - best effort
                _log.warning("Langfuse root observation update failed", error=str(exc))
        try:
            self._observation.end()
        except Exception as exc:  # pragma: no cover - best effort
            _log.warning("Langfuse root observation end failed", error=str(exc))
        with suppress(Exception):
            self._attrs_scope.__exit__(None, None, None)
        with suppress(Exception):
            self._scope.__exit__(None, None, None)
        self._finished = True
        if self._flush_on_run:
            self.flush()

    def flush(self) -> None:
        try:
            flush = getattr(self._client, "flush", None)
            if callable(flush):
                flush(timeout=AGENT_TRACE_FLUSH_TIMEOUT_SECONDS)
        except Exception as exc:  # pragma: no cover - best effort
            _log.warning("Langfuse trace flush failed", error=str(exc))

    def close(self) -> None:
        self.flush()


class TraceExporter:
    """Abstract facade for request-scoped trace exporters."""

    enabled = False

    def start_run(self, trace_context: TraceRunContext) -> TraceRunHandle:
        raise NotImplementedError

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


class NoopTraceExporter(TraceExporter):
    """Trace exporter used when Langfuse is unavailable or disabled."""

    enabled = False

    def __init__(self, reason: str = "trace export disabled") -> None:
        self.reason = reason

    def start_run(self, trace_context: TraceRunContext) -> NoopTraceRun:
        return NoopTraceRun(trace_context=trace_context)


class LangfuseTraceExporter(TraceExporter):
    """Langfuse-backed trace exporter."""

    enabled = True

    def __init__(self, client: Any) -> None:
        self._client = client

    def start_run(self, trace_context: TraceRunContext) -> LangfuseTraceRun:
        from langfuse import propagate_attributes

        root_input = (
            {
                "message": sanitize_trace_value(
                    trace_context.user_message,
                    capture_content=AGENT_TRACE_CAPTURE_CONTENT,
                    preview_chars=AGENT_TRACE_PREVIEW_CHARS,
                ),
                "thread_id": trace_context.thread_id,
                "request_id": trace_context.request_id,
                "stream_id": trace_context.stream_id,
            }
            if AGENT_TRACE_CAPTURE_CONTENT
            else {
                "message": sanitize_trace_value(
                    trace_context.user_message,
                    capture_content=False,
                    preview_chars=AGENT_TRACE_PREVIEW_CHARS,
                ),
                "thread_id": trace_context.thread_id,
                "request_id": trace_context.request_id,
                "stream_id": trace_context.stream_id,
            }
        )
        root_metadata = {
            "runtime": trace_context.runtime,
            "external_trace_id": trace_context.trace_id,
            "request_id": trace_context.request_id,
            "thread_id": trace_context.thread_id,
            "stream_id": trace_context.stream_id,
            "app_env": APP_ENV,
        }
        root_scope = self._client.start_as_current_observation(
            name="restricted_single_agent",
            as_type="agent",
            input=root_input,
            metadata=root_metadata,
            trace_context={"trace_id": trace_context.langfuse_trace_id},
            end_on_exit=False,
        )
        root_observation = root_scope.__enter__()
        attrs_scope = propagate_attributes(
            user_id=trace_context.user_identity or None,
            session_id=trace_context.thread_id or None,
            metadata={
                "request_id": trace_context.request_id,
                "trace_id": trace_context.trace_id,
                "stream_id": trace_context.stream_id,
                "runtime": trace_context.runtime,
            },
            tags=["fault_diagnosis", "single_agent", trace_context.runtime],
            trace_name="restricted_single_agent",
            as_baggage=False,
        )
        attrs_scope.__enter__()
        return LangfuseTraceRun(
            client=self._client,
            scope=root_scope,
            observation=root_observation,
            attrs_scope=attrs_scope,
            trace_context=trace_context,
            capture_content=AGENT_TRACE_CAPTURE_CONTENT,
            preview_chars=AGENT_TRACE_PREVIEW_CHARS,
            flush_on_run=AGENT_TRACE_FLUSH_ON_RUN,
        )

    def flush(self) -> None:
        try:
            flush = getattr(self._client, "flush", None)
            if callable(flush):
                flush(timeout=AGENT_TRACE_FLUSH_TIMEOUT_SECONDS)
        except Exception as exc:  # pragma: no cover - best effort
            _log.warning("Langfuse exporter flush failed", error=str(exc))

    def shutdown(self) -> None:
        self.flush()


def build_trace_exporter() -> TraceExporter:
    """Build the configured trace exporter."""

    if AGENT_TRACE_BACKEND != "langfuse":
        return NoopTraceExporter(reason="AGENT_TRACE_BACKEND=none")
    try:
        client = _build_langfuse_client()
    except Exception as exc:
        _log.warning("Langfuse trace export disabled", error=str(exc), backend=AGENT_TRACE_BACKEND)
        return NoopTraceExporter(reason=str(exc))
    _log.info(
        "Langfuse trace export enabled",
        backend=AGENT_TRACE_BACKEND,
        app_env=APP_ENV,
        capture_content=AGENT_TRACE_CAPTURE_CONTENT,
    )
    return LangfuseTraceExporter(client)


def get_trace_exporter() -> TraceExporter:
    """Return the cached trace exporter."""

    global _TRACE_EXPORTER
    with _TRACE_EXPORTER_LOCK:
        if _TRACE_EXPORTER is None:
            _TRACE_EXPORTER = build_trace_exporter()
        return _TRACE_EXPORTER


def reset_trace_exporter() -> None:
    """Clear the cached exporter; mainly intended for tests."""

    global _TRACE_EXPORTER
    with _TRACE_EXPORTER_LOCK:
        _TRACE_EXPORTER = None


def shutdown_trace_exporter() -> None:
    """Best-effort exporter shutdown."""

    exporter = get_trace_exporter()
    try:
        exporter.shutdown()
    except Exception as exc:  # pragma: no cover - best effort
        _log.warning("Trace exporter shutdown failed", error=str(exc))


def write_local_trace(trace_payload: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> str | None:
    """Persist one completed trace snapshot locally as JSONL when enabled."""

    if not AGENT_TRACE_LOCAL_LOG:
        return None
    envelope = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "metadata": sanitize_trace_value(
            metadata or {},
            capture_content=True,
            preview_chars=AGENT_TRACE_PREVIEW_CHARS,
        ),
        "trace": sanitize_trace_value(
            trace_payload,
            capture_content=AGENT_TRACE_CAPTURE_CONTENT,
            preview_chars=AGENT_TRACE_PREVIEW_CHARS,
        ),
    }
    try:
        os.makedirs(os.path.dirname(AGENT_TRACE_LOCAL_LOG_PATH), exist_ok=True)
        with open(AGENT_TRACE_LOCAL_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, ensure_ascii=False, default=str))
            handle.write("\n")
        return AGENT_TRACE_LOCAL_LOG_PATH
    except Exception as exc:  # pragma: no cover - local diagnostics are best effort
        _log.warning("本地 trace 写入失败", path=AGENT_TRACE_LOCAL_LOG_PATH, error=str(exc))
        return None
