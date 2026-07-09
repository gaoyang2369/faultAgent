"""Developer-friendly rendering for Agent trace records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_TRACE_PATH = Path("trash/run/agent-trace.jsonl")
DEFAULT_OUT_DIR = Path("trash/run/traces")
SLOW_EXACT_MATCH_MS = 1000.0


def normalize_record(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        trace = value.model_dump(mode="json")
        return {"metadata": _metadata_from_trace(trace), "trace": trace}
    if isinstance(value, dict) and value.get("schema_version") == "agent_trace.v1":
        return {"metadata": _metadata_from_trace(value), "trace": value}
    if isinstance(value, dict):
        return value
    return {"metadata": {}, "trace": {"spans": []}}


def normalize_trace(record: dict[str, Any]) -> dict[str, Any]:
    trace = record.get("trace")
    if isinstance(trace, dict):
        return trace
    if record.get("schema_version") == "agent_trace.v1":
        return record
    return {"spans": [], "metadata": {}, **record}


def select_trace_record(path: Path, *, trace_id: str = "", latest: bool = False) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Trace file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Trace file is empty: {path}")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        return normalize_record(data)

    selected: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            continue
        item_trace = normalize_trace(item)
        item_trace_id = str(item_trace.get("trace_id") or item.get("metadata", {}).get("trace_id") or "")
        if trace_id:
            if item_trace_id == trace_id:
                selected = item
        elif latest:
            selected = item
        else:
            selected = item
    if selected is None:
        suffix = f" trace_id={trace_id}" if trace_id else ""
        raise ValueError(f"No trace entry found in {path}{suffix}")
    return selected


def render_compact_summary(
    record_or_trace: Any,
    *,
    trace_path: str | None = None,
    pretty_json_path: str | None = None,
    markdown_path: str | None = None,
    verbose: bool = False,
) -> str:
    record = normalize_record(record_or_trace)
    trace = normalize_trace(record)
    metadata = {**dict(trace.get("metadata") or {}), **dict(record.get("metadata") or {})}
    spans = list(trace.get("spans") or [])
    route = _span_by_name(spans, "skill.route")
    goal = _span_by_name(spans, "goal.build")
    context = _span_by_name(spans, "context.resolve")
    evidence = _span_by_name(spans, "evidence.ledger")
    guardrail = _span_by_name(spans, "guardrail.check")
    output = _span_by_name(spans, "answer.render")
    slowest = _slowest_work_span(spans)
    warnings = _warnings(trace, evidence, guardrail, spans)

    lines = [
        (
            f"[AgentTrace] {trace.get('status', metadata.get('status', ''))} "
            f"{_fmt_ms(trace.get('duration_ms', 0))} request_id={trace.get('request_id') or metadata.get('request_id', '')} "
            f"trace_id={trace.get('trace_id') or metadata.get('trace_id', '')}"
        ),
        f"User: {_request_preview(spans)}",
        f"Route: {(_attrs(goal)).get('task_family', '')} / {(_attrs(route)).get('primary_skill', '')}",
        f"Context: {(_attrs(context)).get('relation_to_previous', '')} reuse={(_attrs(context)).get('reuse_decision', '')}",
        f"Plan: {_plan_summary(spans)}",
        f"Tool: {_tool_summary(spans)}",
        f"Evidence: {_evidence_summary(evidence, guardrail)}",
        f"Output: {_output_summary(output)}",
        f"Slowest: {_span_label(slowest)} {_fmt_ms((slowest or {}).get('duration_ms', 0))}" if slowest else "Slowest: none",
        f"Trace: {trace_path or 'not_written'}",
    ]
    if pretty_json_path:
        lines.append(f"Pretty: {pretty_json_path}")
    if markdown_path:
        lines.append(f"Markdown: {markdown_path}")
    if warnings:
        lines.append(f"Warnings: {', '.join(warnings)}")
    if verbose:
        thread_id = str(trace.get("thread_id") or metadata.get("thread_id") or "")
        stream_id = str(trace.get("stream_id") or metadata.get("stream_id") or "")
        counts = _event_counts(record, trace)
        lines.append(f"TraceMeta: thread_id={_short_id(thread_id)} stream_id={stream_id} {counts}")
    return "\n".join(lines)


def render_markdown(
    record_or_trace: Any,
    *,
    trace_path: str | None = None,
    pretty_json_path: str | None = None,
    markdown_path: str | None = None,
    verbose: bool = False,
) -> str:
    record = normalize_record(record_or_trace)
    trace = normalize_trace(record)
    trace_id = trace.get("trace_id") or record.get("metadata", {}).get("trace_id") or "unknown"
    summary = render_compact_summary(
        record,
        trace_path=trace_path,
        pretty_json_path=pretty_json_path,
        markdown_path=markdown_path,
        verbose=verbose,
    )
    return f"# Agent Trace {trace_id}\n\n```text\n{summary}\n```\n"


def render_pretty_json(record_or_trace: Any) -> str:
    return json.dumps(normalize_record(record_or_trace), ensure_ascii=False, indent=2, default=str) + "\n"


def write_pretty_json(record_or_trace: Any, *, out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"trace_{_trace_file_id(record_or_trace)}.pretty.json"
    path.write_text(render_pretty_json(record_or_trace), encoding="utf-8")
    return str(path)


def write_markdown(
    record_or_trace: Any,
    *,
    out_dir: Path,
    trace_path: str | None = None,
    pretty_json_path: str | None = None,
    verbose: bool = False,
) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"trace_{_trace_file_id(record_or_trace)}.md"
    path.write_text(
        render_markdown(
            record_or_trace,
            trace_path=trace_path,
            pretty_json_path=pretty_json_path,
            markdown_path=str(path),
            verbose=verbose,
        ),
        encoding="utf-8",
    )
    return str(path)


def _metadata_from_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": trace.get("request_id", ""),
        "thread_id": trace.get("thread_id", ""),
        "trace_id": trace.get("trace_id", ""),
        "stream_id": trace.get("stream_id", ""),
        "status": trace.get("status", ""),
    }


def _trace_file_id(record_or_trace: Any) -> str:
    record = normalize_record(record_or_trace)
    trace = normalize_trace(record)
    trace_id = str(trace.get("trace_id") or record.get("metadata", {}).get("trace_id") or "unknown")
    return _safe_name(trace_id)


def _span_by_name(spans: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next((span for span in spans if span.get("name") == name), {})


def _attrs(span: dict[str, Any]) -> dict[str, Any]:
    attrs = span.get("attributes") if isinstance(span, dict) else {}
    return attrs if isinstance(attrs, dict) else {}


def _request_preview(spans: list[dict[str, Any]]) -> str:
    message = _attrs(_span_by_name(spans, "chat.request")).get("message") or {}
    if isinstance(message, dict):
        if message.get("preview"):
            return str(message["preview"])
        return f"chars={message.get('chars', 0)} sha256_12={message.get('sha256_12', '')}"
    return str(message or "")


def _plan_summary(spans: list[dict[str, Any]]) -> str:
    parts = []
    for span in spans:
        if span.get("kind") != "node":
            continue
        attrs = _attrs(span)
        node_id = attrs.get("node_id") or str(span.get("name", "")).removeprefix("node.")
        reason = f":{attrs.get('reason')}" if span.get("status") == "skipped" and attrs.get("reason") else ""
        if span.get("status") == "skipped":
            parts.append(f"{node_id} {span.get('status')}{reason}")
        else:
            parts.append(f"{node_id} {span.get('status')}{reason} {_fmt_ms(span.get('duration_ms', 0))}")
    return "; ".join(parts) or "none"


def _tool_summary(spans: list[dict[str, Any]]) -> str:
    parts = []
    for span in spans:
        if span.get("kind") != "tool":
            continue
        attrs = _attrs(span)
        sources = attrs.get("selected_sources") or []
        first = sources[0] if isinstance(sources, list) and sources else {}
        source = f"{first.get('file', '')}#{first.get('page', '')}".strip("#")
        latency = attrs.get("latency_ms", span.get("duration_ms", 0))
        phases = _phase_summary(attrs.get("phase_latencies_ms"))
        cold = f" cold_start={attrs.get('cold_start')}" if "cold_start" in attrs else ""
        parts.append(
            (
                f"{str(span.get('name', '')).removeprefix('tool.')} {attrs.get('match_type') or attrs.get('retrieval_mode', '')} "
                f"mode={attrs.get('retrieval_mode', '')} hit={attrs.get('hit_count', 0)} "
                f"source={source or 'unknown'} latency={_fmt_ms(latency)}{phases}{cold}"
            ).strip()
        )
    return "; ".join(parts) or "none"


def _phase_summary(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    parts = [f"{key}:{_fmt_ms(item)}" for key, item in value.items()]
    return f" phases={','.join(parts)}"


def _evidence_summary(evidence: dict[str, Any], guardrail: dict[str, Any]) -> str:
    attrs = _attrs(evidence)
    final_claim_ids = attrs.get("final_claim_ids") or []
    final_count = len(final_claim_ids) if isinstance(final_claim_ids, list) else 0
    guardrail_status = "blocked" if _attrs(guardrail).get("blocked") else "pass"
    return (
        f"evidence={attrs.get('evidence_count', 0)} "
        f"claim={attrs.get('claim_count', 0)} "
        f"final_claim={final_count} "
        f"guardrail={guardrail_status}"
    )


def _output_summary(output: dict[str, Any]) -> str:
    attrs = _attrs(output)
    return (
        f"{attrs.get('answer_template', '')} "
        f"llm_used={str(attrs.get('llm_used', False)).lower()} "
        f"answer_len={attrs.get('answer_length', 0)}"
    ).strip()


def _slowest_work_span(spans: list[dict[str, Any]]) -> dict[str, Any]:
    tool_candidates = [span for span in spans if span.get("kind") == "tool"]
    if tool_candidates:
        return max(tool_candidates, key=lambda span: _float(span.get("duration_ms")), default={})
    candidates = [span for span in spans if span.get("kind") == "node"]
    return max(candidates, key=lambda span: _float(span.get("duration_ms")), default={})


def _span_label(span: dict[str, Any]) -> str:
    if not span:
        return "none"
    return str(span.get("name") or span.get("span_id") or "unknown")


def _warnings(
    trace: dict[str, Any],
    evidence: dict[str, Any],
    guardrail: dict[str, Any],
    spans: list[dict[str, Any]],
) -> list[str]:
    values: list[str] = []
    raw = _attrs(evidence).get("warnings") or []
    if isinstance(raw, list):
        values.extend(str(item) for item in raw if str(item or "").strip())
    missing = _attrs(guardrail).get("missing_evidence") or []
    stale = _attrs(guardrail).get("stale_evidence") or []
    if missing:
        values.append("missing_evidence")
    if stale:
        values.append("stale_evidence")
    for span in spans:
        if span.get("status") in {"failed", "blocked", "cancelled"}:
            values.append(f"{span.get('name')}:{span.get('status')}")
        attrs = _attrs(span)
        latency = _float(attrs.get("latency_ms", span.get("duration_ms")))
        if (
            span.get("name") == "tool.kb.search"
            and latency > SLOW_EXACT_MATCH_MS
            and attrs.get("retrieval_mode") == "fault_code_exact_match"
        ):
            values.append("fault_code_exact_match_slow")
    for error in trace.get("errors") or []:
        if isinstance(error, dict) and error.get("message"):
            values.append(str(error["message"]))
    return list(dict.fromkeys(values))


def _event_counts(record: dict[str, Any], trace: dict[str, Any]) -> str:
    metadata = {**dict(trace.get("metadata") or {}), **dict(record.get("metadata") or {})}
    top_level = metadata.get("top_level_event_count", len(trace.get("events") or []))
    nested = metadata.get("nested_event_count", sum(len(span.get("events") or []) for span in trace.get("spans") or []))
    return f"top_level_event_count={top_level} nested_event_count={nested}"


def _short_id(value: str, *, limit: int = 16) -> str:
    if not value or len(value) <= limit:
        return value
    return f"{value[:8]}...{value[-6:]}"


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value)
    return cleaned.strip("._") or "unknown"


def _fmt_ms(value: Any) -> str:
    number = _float(value)
    if abs(number) >= 100:
        return f"{int(round(number))}ms"
    if abs(number - round(number)) < 0.05:
        return f"{int(round(number))}ms"
    return f"{number:.1f}ms"


def _float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
