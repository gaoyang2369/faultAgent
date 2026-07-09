"""Export canonical trace envelopes to local JSONL, console, and Langfuse."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fault_diagnosis.platform.logging import get_logger
from fault_diagnosis.platform.settings import (
    AGENT_TRACE_CONSOLE,
    AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
    AGENT_TRACE_CONSOLE_SPANS,
    AGENT_TRACE_CONSOLE_SUMMARY,
    AGENT_TRACE_CONSOLE_VERBOSE,
    AGENT_TRACE_LOCAL_LOG,
    AGENT_TRACE_LOCAL_LOG_PATH,
    AGENT_TRACE_MARKDOWN,
    AGENT_TRACE_PRETTY_JSON,
    AGENT_TRACE_PREVIEW_CHARS,
)

from .payloads import sanitize_trace_value
from .trace_rendering import render_compact_summary, write_markdown, write_pretty_json
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
    payload = build_trace_record(envelope, metadata=metadata, runtime_events=legacy_runtime_events)
    try:
        os.makedirs(os.path.dirname(AGENT_TRACE_LOCAL_LOG_PATH), exist_ok=True)
        with open(AGENT_TRACE_LOCAL_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str))
            handle.write("\n")
        return AGENT_TRACE_LOCAL_LOG_PATH
    except Exception as exc:  # pragma: no cover - diagnostics are best effort.
        _log.warning("本地 trace 写入失败", path=AGENT_TRACE_LOCAL_LOG_PATH, error=str(exc))
        return None


def write_trace_side_artifacts(
    envelope: TraceEnvelope,
    *,
    metadata: dict[str, Any] | None = None,
    runtime_events: list[dict[str, Any]] | None = None,
    trace_path: str | None = None,
) -> dict[str, str]:
    record = build_trace_record(envelope, metadata=metadata, runtime_events=runtime_events)
    paths: dict[str, str] = {}
    out_dir = _trace_artifact_dir()
    try:
        if AGENT_TRACE_PRETTY_JSON:
            paths["pretty_json_path"] = write_pretty_json(record, out_dir=out_dir)
        if AGENT_TRACE_MARKDOWN:
            paths["markdown_path"] = write_markdown(
                record,
                out_dir=out_dir,
                trace_path=trace_path,
                pretty_json_path=paths.get("pretty_json_path"),
                verbose=AGENT_TRACE_CONSOLE_VERBOSE,
            )
    except Exception as exc:  # pragma: no cover - diagnostics are best effort.
        _log.warning("Trace side artifact write failed", trace_id=envelope.trace_id, error=str(exc))
    return paths


def build_trace_record(
    envelope: TraceEnvelope,
    *,
    metadata: dict[str, Any] | None = None,
    runtime_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    envelope_dict = envelope.model_dump(mode="json")
    safe_metadata = sanitize_trace_value(metadata or {}, capture_content=True, preview_chars=AGENT_TRACE_PREVIEW_CHARS)
    events = runtime_events or []
    top_level_event_count = len(envelope.events)
    nested_event_count = sum(len(span.events) for span in envelope.spans)
    return {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            **safe_metadata,
            "request_id": envelope.request_id,
            "thread_id": envelope.thread_id,
            "trace_id": envelope.trace_id,
            "stream_id": envelope.stream_id,
            "status": envelope.status,
            "span_count": len(envelope.spans),
            "event_count": top_level_event_count,
            "top_level_event_count": top_level_event_count,
            "nested_event_count": nested_event_count,
        },
        "trace": envelope_dict,
        "runtime_events": events,
        "legacy_runtime_events": events,
    }


def _trace_artifact_dir() -> Path:
    base_dir = os.path.dirname(AGENT_TRACE_LOCAL_LOG_PATH) or "."
    return Path(base_dir) / "traces"


def write_console_trace_envelope(
    envelope: TraceEnvelope,
    *,
    trace_path: str | None = None,
    pretty_json_path: str | None = None,
    markdown_path: str | None = None,
) -> None:
    if not AGENT_TRACE_CONSOLE:
        return
    if AGENT_TRACE_CONSOLE_SUMMARY:
        _log.info(
            render_compact_summary(
                envelope,
                trace_path=trace_path,
                pretty_json_path=pretty_json_path,
                markdown_path=markdown_path,
                verbose=AGENT_TRACE_CONSOLE_VERBOSE,
            )
        )
    if not AGENT_TRACE_CONSOLE_SPANS:
        return
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
    summary = str(attrs) if AGENT_TRACE_CONSOLE_VERBOSE else _summary_for_span(span)
    log_method = _log.warning if span.status in {"blocked", "failed", "cancelled"} or span.error else _log.info
    log_method(
        span.name,
        trace_id=envelope.trace_id,
        thread_id=envelope.thread_id if AGENT_TRACE_CONSOLE_VERBOSE else "",
        stream_id=envelope.stream_id if AGENT_TRACE_CONSOLE_VERBOSE else "",
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
    if span.name == "goal.build":
        return f"task_family={attrs.get('task_family')}; primary_goal={attrs.get('primary_goal')}; fault_codes={attrs.get('fault_codes', [])}"
    if span.name == "skill.route":
        return f"primary_skill={attrs.get('primary_skill')}; selected_skills={attrs.get('selected_skills', [])}"
    if span.name == "context.resolve":
        return f"relation={attrs.get('relation_to_previous')}; reuse={attrs.get('reuse_decision')}"
    if span.name == "plan.validate":
        return f"enabled_nodes={attrs.get('enabled_nodes', [])}; skipped_nodes={attrs.get('skipped_nodes', [])}"
    if span.name == "node.rag":
        return f"tools={attrs.get('required_tools', [])}"
    if span.kind == "node":
        return f"node_id={attrs.get('node_id')}; node_type={attrs.get('node_type')}; reason={attrs.get('reason', '')}"
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
    return "" if not AGENT_TRACE_CONSOLE_VERBOSE else str(attrs)


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
