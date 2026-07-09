"""Runtime state and result contracts for Agent Engine V2."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import EvidenceLedger, ExecutionPlan, NodeResult, NodeStatus, OutputFrame
from ..evidence import create_ledger, finalize_ledger
from ..evidence.ledger import EvidenceLedgerWriter
from fault_diagnosis.domain.security.contracts import AuthContext

RuntimeStatus = Literal["completed", "blocked", "failed", "cancelled"]


class CancelToken:
    """Small cancellation handle used by the V2 runtime."""

    def __init__(self) -> None:
        self.cancelled = False
        self.reason = "user_stop"

    def cancel(self, reason: str = "user_stop") -> None:
        self.cancelled = True
        self.reason = reason or "user_stop"


class RuntimeTraceEvent(BaseModel):
    """One runtime trace event, serializable for snapshots and tests."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "runtime_trace_event.v1"
    event_type: str
    node_id: str = ""
    node_type: str = ""
    status: str = ""
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: float | None = None
    retry_count: int = 0
    error: dict[str, Any] | None = None
    timestamp: str = Field(default_factory=lambda: _timestamp())
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeState(BaseModel):
    """Mutable execution state for one V2 runtime invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    schema_version: str = "runtime_state.v1"
    plan: ExecutionPlan
    trace_id: str = ""
    thread_id: str = ""
    request_id: str = ""
    status: RuntimeStatus | Literal["running"] = "running"
    auth_context: AuthContext | None = None
    cancel_token: CancelToken = Field(default_factory=CancelToken, exclude=True)
    node_results: list[NodeResult] = Field(default_factory=list)
    evidence_ledger: EvidenceLedger = Field(default_factory=EvidenceLedger)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    trace_events: list[RuntimeTraceEvent] = Field(default_factory=list)
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str = Field(default_factory=lambda: _timestamp())

    def add_trace(self, event_type: str, **payload: Any) -> RuntimeTraceEvent:
        event = RuntimeTraceEvent(event_type=event_type, **payload)
        self.trace_events.append(event)
        return event

    def append_node_result(self, result: NodeResult) -> None:
        self.node_results.append(result)

    def commit_evidence(self, node: dict[str, Any], evidence_items: list[dict[str, Any]]) -> list[str]:
        return EvidenceLedgerWriter(self.evidence_ledger, auth_context=self.auth_context).commit_evidence(
            evidence_items,
            node=node,
        ).refs

    def commit_claims(self, claims: list[dict[str, Any]]) -> list[str]:
        return EvidenceLedgerWriter(self.evidence_ledger, auth_context=self.auth_context).commit_claims(claims).refs

    def initialize_ledger(self) -> None:
        if self.evidence_ledger.ledger_id:
            return
        self.evidence_ledger = create_ledger(
            trace_id=self.trace_id or self.request_id or self.plan.plan_id,
            task={
                "trace_id": self.trace_id,
                "thread_id": self.thread_id,
                "request_id": self.request_id,
                "plan_id": self.plan.plan_id,
                "required_evidence": list(self.plan.required_evidence),
            },
            auth_context=self.auth_context,
        )

    def finalize_ledger(self) -> None:
        artifact_refs = [
            {"artifact_type": key, "available": True}
            for key, value in self.artifacts.items()
            if value is not None
        ]
        finalize_ledger(self.evidence_ledger, auth_context=self.auth_context, artifact_refs=artifact_refs)

    def trace_payload(self) -> dict[str, Any]:
        events = [event.model_dump(mode="json") for event in self.trace_events]
        return {
            "runtime": "agent_engine_v2",
            "status": self.status,
            "trace_id": self.trace_id,
            "thread_id": self.thread_id,
            "request_id": self.request_id,
            "plan_id": self.plan.plan_id,
            "node_order": [
                event.node_id
                for event in self.trace_events
                if event.event_type == "node_status" and event.status in {"completed", "blocked", "failed", "skipped", "cancelled"}
            ],
            "events": events,
            "errors": list(self.errors),
            "interrupts": list(self.interrupts),
            "evidence_quality": dict(self.evidence_ledger.quality_checks),
        }


class RuntimeResult(BaseModel):
    """Final result of a V2 runtime run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "runtime_result.v1"
    status: RuntimeStatus
    node_results: list[NodeResult] = Field(default_factory=list)
    evidence_ledger: EvidenceLedger = Field(default_factory=EvidenceLedger)
    output_frame: OutputFrame = Field(default_factory=OutputFrame)
    trace: dict[str, Any] = Field(default_factory=dict)
    complete_payload: dict[str, Any] = Field(default_factory=dict)
    cancel_payload: dict[str, Any] | None = None


def build_complete_payload(
    *,
    state: RuntimeState,
    status: RuntimeStatus,
    final_content: str = "",
    output_frame: OutputFrame | None = None,
    cancelled: bool = False,
    cancel_reason: str | None = None,
) -> dict[str, Any]:
    from ..output.sse_projection import project_complete

    return project_complete(
        state=state,
        status=status,
        output_frame=output_frame,
        final_content=final_content,
        cancelled=cancelled,
        cancel_reason=cancel_reason,
    )


def node_result(
    *,
    node: dict[str, Any],
    status: NodeStatus,
    input_summary: str,
    output: Any = None,
    evidence_refs: list[str] | None = None,
    tool_call_refs: list[str] | None = None,
    error: dict[str, Any] | None = None,
    retry_count: int = 0,
    duration_ms: float = 0.0,
) -> NodeResult:
    return NodeResult(
        node_id=str(node.get("node_id") or ""),
        node_type=str(node.get("node_type") or ""),
        status=status,
        input_summary=input_summary,
        output=output if output is not None else {},
        evidence_refs=list(evidence_refs or []),
        tool_call_refs=list(tool_call_refs or []),
        error=error,
        retry_count=retry_count,
        duration_ms=max(0.0, duration_ms),
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
