"""Optional trace export bridge for the Agent Engine V2 runtime."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterator, Protocol

from fault_diagnosis.platform.logging import get_logger
from fault_diagnosis.platform.settings import (
    AGENT_TRACE_BACKEND,
    AGENT_TRACE_CAPTURE_CONTENT,
    AGENT_TRACE_CONSOLE,
    AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
    AGENT_TRACE_CONSOLE_VERBOSE,
    AGENT_TRACE_FLUSH_ON_RUN,
    AGENT_TRACE_FLUSH_TIMEOUT_SECONDS,
    AGENT_TRACE_LOCAL_LOG,
    AGENT_TRACE_LOCAL_LOG_PATH,
    AGENT_TRACE_PREVIEW_CHARS,
    APP_ENV,
)
from .payloads import sanitize_trace_value
from .trace_exporters import export_langfuse_trace_envelope, write_console_trace_envelope, write_local_trace_envelope
from .trace_recorder import canonical_trace_from_payload
from .trace_schema import TraceEnvelope

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
    runtime: str = "agent_engine_v2"
    model_name: str | None = None

    @property
    def langfuse_trace_id(self) -> str:
        seed = self.trace_id or self.request_id or self.thread_id or "agent_engine_v2"
        return _normalize_trace_id(seed)


class TraceObservationHandle(Protocol):
    """Minimal observation interface used by the Agent Engine V2 runtime."""

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
            name="agent_engine_v2",
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
            tags=["fault_diagnosis", "agent_engine_v2", trace_context.runtime],
            trace_name="agent_engine_v2",
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


def write_local_trace(trace_payload: dict[str, Any] | TraceEnvelope, *, metadata: dict[str, Any] | None = None) -> str | None:
    """Persist one completed trace snapshot locally as JSONL when enabled."""

    envelope = _ensure_canonical_envelope(trace_payload, metadata=metadata)
    legacy_events = _legacy_events(trace_payload)
    return write_local_trace_envelope(envelope, metadata=metadata, legacy_runtime_events=legacy_events)


def export_trace_snapshot(
    trace_payload: dict[str, Any] | TraceEnvelope,
    *,
    metadata: dict[str, Any] | None = None,
    trace_context: TraceRunContext | None = None,
    output: Any | None = None,
    error: str | None = None,
    legacy_runtime_events: list[dict[str, Any]] | None = None,
) -> str | None:
    """Export one completed runtime trace to configured local, console, and backend sinks."""

    envelope = _ensure_canonical_envelope(
        trace_payload,
        metadata=metadata,
        trace_context=trace_context,
        output=output,
        error=error,
    )
    local_path = write_local_trace_envelope(
        envelope,
        metadata=metadata,
        legacy_runtime_events=legacy_runtime_events if legacy_runtime_events is not None else _legacy_events(trace_payload),
    )
    write_console_trace_envelope(envelope)
    if trace_context is not None:
        export_langfuse_trace_envelope(
            envelope,
            trace_context=trace_context,
            start_run=get_trace_exporter().start_run,
            output=output,
            error=error,
        )
    return local_path


def write_console_trace(trace_payload: dict[str, Any] | TraceEnvelope, *, metadata: dict[str, Any] | None = None) -> None:
    """Print a compact step-by-step runtime trace to the backend console when enabled."""

    write_console_trace_envelope(_ensure_canonical_envelope(trace_payload, metadata=metadata))


def _write_console_trace_event(event: dict[str, Any], *, trace_id: str, thread_id: str, stream_id: str) -> None:
    event_type = str(event.get("event_type") or "trace_event")
    status = str(event.get("status") or "")
    node_id = str(event.get("node_id") or "")
    node_type = str(event.get("node_type") or "")
    if not AGENT_TRACE_CONSOLE_VERBOSE and event_type == "node_status" and status == "pending":
        return

    input_summary = str(event.get("input_summary") or "")
    output_summary = str(event.get("output_summary") or "")
    error = event.get("error")
    log_method = _log.warning if status in {"failed", "blocked", "cancelled"} or error else _log.info
    log_method(
        "Agent trace event",
        trace_id=trace_id,
        thread_id=thread_id,
        stream_id=stream_id,
        stage=node_type or event_type,
        node_id=node_id,
        node_type=node_type,
        status=status,
        duration_ms=event.get("duration_ms"),
        input_preview=_preview(input_summary, AGENT_TRACE_CONSOLE_PREVIEW_CHARS)
        if AGENT_TRACE_CONSOLE_VERBOSE or status in {"running", "failed", "blocked"}
        else "",
        result_preview=_preview(output_summary, AGENT_TRACE_CONSOLE_PREVIEW_CHARS),
        error=_preview(error, AGENT_TRACE_CONSOLE_PREVIEW_CHARS) if error else "",
    )


def _export_backend_trace(
    trace_payload: dict[str, Any],
    *,
    trace_context: TraceRunContext,
    output: Any | None = None,
    error: str | None = None,
) -> None:
    try:
        trace_run = get_trace_exporter().start_run(trace_context)
        for event in trace_payload.get("events", []):
            if not isinstance(event, dict):
                continue
            observation = trace_run.start_observation(
                name=_observation_name(event),
                as_type="span",
                input={"summary": event.get("input_summary")} if event.get("input_summary") else None,
                output={"summary": event.get("output_summary")} if event.get("output_summary") else None,
                metadata={key: value for key, value in event.items() if key not in {"input_summary", "output_summary"}},
                level="ERROR" if event.get("error") else None,
                status_message=_normalize_status_message(event.get("status") or event.get("event_type") or ""),
            )
            observation.finish(
                status=str(event.get("status") or "completed"),
                error=str(event.get("error")) if event.get("error") else None,
            )
        trace_run.finish(
            status=str(trace_payload.get("status") or ("failed" if error else "completed")),
            output=output,
            error=error,
            metadata={
                "event_count": len(trace_payload.get("events", [])),
                "node_order": trace_payload.get("node_order", []),
            },
        )
    except Exception as exc:  # pragma: no cover - observability must not break chat.
        _log.warning("Trace export failed", trace_id=trace_context.trace_id, error=str(exc))


def _observation_name(event: dict[str, Any]) -> str:
    node_type = str(event.get("node_type") or "").strip()
    node_id = str(event.get("node_id") or "").strip()
    event_type = str(event.get("event_type") or "trace_event").strip()
    if node_type and node_id:
        return f"{event_type}:{node_type}:{node_id}"
    if node_type:
        return f"{event_type}:{node_type}"
    return event_type or "trace_event"


def _preview(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}..."


def _ensure_canonical_envelope(
    trace_payload: dict[str, Any] | TraceEnvelope,
    *,
    metadata: dict[str, Any] | None = None,
    trace_context: TraceRunContext | None = None,
    output: Any | None = None,
    error: str | None = None,
) -> TraceEnvelope:
    if isinstance(trace_payload, TraceEnvelope):
        return trace_payload
    if isinstance(trace_payload, dict) and trace_payload.get("schema_version") == "agent_trace.v1":
        return TraceEnvelope.model_validate(trace_payload)
    payload = trace_payload if isinstance(trace_payload, dict) else {}
    return canonical_trace_from_payload(
        trace_payload=payload,
        trace_context=trace_context,
        metadata=metadata,
        output=output,
        error=error,
    )


def _legacy_events(trace_payload: dict[str, Any] | TraceEnvelope) -> list[dict[str, Any]]:
    if isinstance(trace_payload, TraceEnvelope):
        return []
    if isinstance(trace_payload, dict):
        events = trace_payload.get("events")
        if isinstance(events, list):
            return [event for event in events if isinstance(event, dict)]
    return []
