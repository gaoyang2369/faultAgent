"""Export canonical trace envelopes to local JSONL, console, and Langfuse."""

from __future__ import annotations

import json
import os
from datetime import timezone, datetime
from typing import Any, Callable

from fault_diagnosis.platform.logging import get_logger
from fault_diagnosis.platform.settings import (
    AGENT_TRACE_CONSOLE,
    AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
    AGENT_TRACE_CONSOLE_VERBOSE,
    AGENT_TRACE_LOCAL_LOG,
    AGENT_TRACE_LOCAL_LOG_PATH,
    AGENT_TRACE_PREVIEW_CHARS,
)

from .payloads import sanitize_trace_value
from .trace_schema import TraceEnvelope, TraceSpan

_log = get_logger("observability.trace")


def write_local_trace_envelope(
    envelope: TraceEnvelope,
    *,
    metadata: dict[str, Any] | None = None,
    legacy_runtime_events: list[dict[str, Any]] | None = None,
) -> str | None:
    if not AGENT_TRACE_LOCAL_LOG:
        return None
    envelope_dict = envelope.model_dump(mode="json")
    safe_metadata = sanitize_trace_value(metadata or {}, capture_content=True, preview_chars=AGENT_TRACE_PREVIEW_CHARS)
    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            **safe_metadata,
            "request_id": envelope.request_id,
            "thread_id": envelope.thread_id,
            "trace_id": envelope.trace_id,
            "stream_id": envelope.stream_id,
            "status": envelope.status,
            "span_count": len(envelope.spans),
            "event_count": len(envelope.events),
        },
        "trace": envelope_dict,
        "legacy_runtime_events": legacy_runtime_events or [],
    }
    try:
        os.makedirs(os.path.dirname(AGENT_TRACE_LOCAL_LOG_PATH), exist_ok=True)
        with open(AGENT_TRACE_LOCAL_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str))
            handle.write("\n")
        return AGENT_TRACE_LOCAL_LOG_PATH
    except Exception as exc:  # pragma: no cover - diagnostics are best effort.
        _log.warning("本地 trace 写入失败", path=AGENT_TRACE_LOCAL_LOG_PATH, error=str(exc))
        return None


def write_console_trace_envelope(envelope: TraceEnvelope) -> None:
    if not AGENT_TRACE_CONSOLE:
        return
    nodes = [
        span.attributes.get("node_id")
        for span in envelope.spans
        if span.kind == "node" and span.status == "completed" and span.attributes.get("node_id")
    ]
    skipped = [
        f"{span.attributes.get('node_id')}:{span.attributes.get('reason') or 'skipped'}"
        for span in envelope.spans
        if span.kind == "node" and span.status == "skipped" and span.attributes.get("node_id")
    ]
    _log.info(
        "Agent trace completed",
        trace_id=envelope.trace_id,
        thread_id=envelope.thread_id,
        stream_id=envelope.stream_id,
        status=envelope.status,
        duration_ms=envelope.duration_ms,
        event_count=len(envelope.events),
        summary=f"nodes={nodes}; skipped={skipped}",
    )
    for span in envelope.spans:
        if span.name == "chat.request":
            continue
        if _should_print_span(span):
            _log_span(span, envelope)


def export_langfuse_trace_envelope(
    envelope: TraceEnvelope,
    *,
    trace_context: Any | None,
    start_run: Callable[[Any], Any],
    output: Any | None = None,
    error: str | None = None,
) -> None:
    if trace_context is None:
        return
    try:
        trace_run = start_run(trace_context)
        for span in envelope.spans:
            if span.name == "chat.request":
                continue
            observation = trace_run.start_observation(
                name=span.name,
                as_type=_langfuse_type(span.kind),
                metadata={
                    **span.attributes,
                    "span_id": span.span_id,
                    "parent_span_id": span.parent_span_id,
                    "kind": span.kind,
                    "duration_ms": span.duration_ms,
                    "retry_count": span.retry_count,
                },
                level="ERROR" if span.error or span.status in {"failed", "blocked"} else None,
                status_message=str(span.error or span.status)[:500],
            )
            observation.finish(status=span.status, error=str(span.error) if span.error else None)
        trace_run.finish(
            status=envelope.status,
            output=output,
            error=error,
            metadata={
                "span_count": len(envelope.spans),
                "event_count": len(envelope.events),
                "planned_nodes": envelope.metadata.get("planned_nodes", []),
                "executed_nodes": envelope.metadata.get("executed_nodes", []),
                "skipped_nodes": envelope.metadata.get("skipped_nodes", []),
            },
        )
    except Exception as exc:  # pragma: no cover - remote tracing is best effort.
        trace_id = getattr(trace_context, "trace_id", envelope.trace_id)
        _log.warning("Trace export failed", trace_id=trace_id, error=str(exc))


def _should_print_span(span: TraceSpan) -> bool:
    if AGENT_TRACE_CONSOLE_VERBOSE:
        return True
    if span.kind == "planner":
        return span.name in {"plan.validate", "skill.route", "goal.build"}
    if span.kind in {"node", "tool", "parser", "evidence", "output", "guardrail"}:
        return span.status in {"completed", "skipped", "blocked", "failed", "cancelled"}
    return False


def _log_span(span: TraceSpan, envelope: TraceEnvelope) -> None:
    attrs = span.attributes
    summary = _summary_for_span(span)
    log_method = _log.warning if span.status in {"blocked", "failed", "cancelled"} or span.error else _log.info
    log_method(
        span.name,
        trace_id=envelope.trace_id,
        thread_id=envelope.thread_id,
        stream_id=envelope.stream_id,
        status=span.status,
        duration_ms=span.duration_ms,
        stage=span.name,
        node_id=attrs.get("node_id", ""),
        node_type=attrs.get("node_type", ""),
        summary=_preview(summary),
        error=_preview(span.error) if span.error else "",
    )
    if AGENT_TRACE_CONSOLE_VERBOSE:
        for event in span.events:
            _log.info(
                "Agent trace lifecycle",
                trace_id=envelope.trace_id,
                stage=span.name,
                status=event.name,
                summary=_preview(event.attributes),
            )


def _summary_for_span(span: TraceSpan) -> str:
    attrs = span.attributes
    if span.name == "plan.validate":
        return f"enabled_nodes={attrs.get('enabled_nodes', [])}; skipped_nodes={attrs.get('skipped_nodes', [])}"
    if span.name == "node.rag":
        return f"tools={attrs.get('required_tools', [])}"
    if span.name == "tool.kb.search":
        sources = attrs.get("selected_sources") or []
        first = sources[0] if isinstance(sources, list) and sources else {}
        return f"match_type={attrs.get('match_type')}; source={first.get('file')}:{first.get('page')}; hit_count={attrs.get('hit_count')}"
    if span.name == "fault_code.entry_parse":
        return f"code={attrs.get('code')}; has_cause={attrs.get('has_cause')}; has_remedy={attrs.get('has_remedy')}"
    if span.name == "answer.render":
        return f"template={attrs.get('answer_template')}; llm_used={attrs.get('llm_used')}"
    if span.name == "guardrail.check":
        return f"evidence_satisfied={attrs.get('evidence_satisfied')}; blocked={attrs.get('blocked')}"
    return str(attrs)


def _langfuse_type(kind: str) -> str:
    if kind == "tool":
        return "tool"
    if kind == "llm":
        return "generation"
    if kind == "request":
        return "agent"
    return "span"


def _preview(value: Any) -> str:
    text = str(value or "")
    limit = AGENT_TRACE_CONSOLE_PREVIEW_CHARS
    return text if len(text) <= limit else f"{text[:limit]}..."

