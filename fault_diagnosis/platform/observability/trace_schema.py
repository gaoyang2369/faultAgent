"""Canonical Agent Engine V2 trace schema."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TraceSpanKind = Literal[
    "request",
    "planner",
    "runtime",
    "node",
    "tool",
    "llm",
    "parser",
    "evidence",
    "output",
    "guardrail",
    "artifact",
]
TraceStatus = Literal["pending", "running", "completed", "skipped", "blocked", "failed", "cancelled"]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TraceEvent(BaseModel):
    """Small event attached to a canonical span."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "agent_trace_event.v1"
    trace_id: str
    span_id: str = ""
    name: str
    timestamp: str = Field(default_factory=utc_now_iso)
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceSpan(BaseModel):
    """One canonical span in the Agent trace tree."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "agent_trace_span.v1"
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    name: str
    kind: TraceSpanKind
    status: TraceStatus = "completed"
    start_time: str = Field(default_factory=utc_now_iso)
    end_time: str = Field(default_factory=utc_now_iso)
    duration_ms: float = 0.0
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[TraceEvent] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    retry_count: int = 0


class TraceEnvelope(BaseModel):
    """Canonical source-of-truth trace envelope for one chat request."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "agent_trace.v1"
    trace_id: str
    request_id: str = ""
    thread_id: str = ""
    stream_id: str = ""
    endpoint: str = ""
    status: TraceStatus = "completed"
    started_at: str = Field(default_factory=utc_now_iso)
    ended_at: str = Field(default_factory=utc_now_iso)
    duration_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    spans: list[TraceSpan] = Field(default_factory=list)
    events: list[TraceEvent] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)

