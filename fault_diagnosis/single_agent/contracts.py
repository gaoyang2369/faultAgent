"""Trace and limit contracts for the restricted single-agent runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TraceEventType = Literal["stage", "decision", "tool_call", "tool_result", "artifact", "final_answer"]


class SingleAgentLimits(BaseModel):
    """Hard limits for one deterministic single-agent run."""

    max_rounds: int = Field(default=6, ge=1)
    max_tool_calls: int = Field(default=4, ge=0)
    allowed_tools: tuple[str, ...] = (
        "sql_db_query_checker",
        "sql_db_query",
        "query_knowledge_base",
        "save_report",
    )


class SingleAgentDecision(BaseModel):
    """Capability decisions made after request understanding."""

    needs_sql: bool = False
    needs_knowledge: bool = False
    needs_report: bool = False
    report_from_previous_artifact: bool = False
    reason: str = ""


class TraceEvent(BaseModel):
    """One normalized trace event emitted by the restricted runtime."""

    event_type: TraceEventType
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    stage: str | None = None
    status: str | None = None
    message: str = ""
    decision: dict[str, Any] | None = None
    tool: str | None = None
    run_id: str | None = None
    input: Any | None = None
    result_preview: str | None = None
    artifact_type: str | None = None
    artifact: dict[str, Any] | None = None
    final_answer: str | None = None
    error: str | None = None
    duration_ms: float | None = None


class AgentTrace(BaseModel):
    """Full per-run trace saved for troubleshooting and audit."""

    trace_id: str
    request_id: str
    thread_id: str
    user_identity: str
    user_message: str
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    finished_at: str | None = None
    status: str = "running"
    limits: SingleAgentLimits = Field(default_factory=SingleAgentLimits)
    events: list[TraceEvent] = Field(default_factory=list)

    def add_event(self, event_type: TraceEventType, **payload: Any) -> TraceEvent:
        event = TraceEvent(event_type=event_type, **payload)
        self.events.append(event)
        return event

    def finish(self, *, status: str, final_answer: str | None = None, error: str | None = None) -> None:
        self.status = status
        self.finished_at = datetime.now().isoformat()
        if final_answer is not None:
            self.add_event("final_answer", stage="final_answer", status=status, final_answer=final_answer)
        elif error is not None:
            self.add_event("final_answer", stage="final_answer", status=status, error=error)
