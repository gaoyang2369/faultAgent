"""Runtime state and result contracts for Agent Engine V2."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import EvidenceLedger, ExecutionPlan, NodeResult, NodeStatus
from ...security.contracts import AuthContext

RuntimeStatus = Literal["completed", "blocked", "failed", "cancelled"]


class CancelToken:
    """Small cancellation handle used by the sidecar runtime."""

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
        refs: list[str] = []
        for index, item in enumerate(evidence_items, start=1):
            payload = dict(item)
            evidence_id = str(payload.get("evidence_id") or f"ev_{node.get('node_id')}_{index}")
            payload["evidence_id"] = evidence_id
            payload.setdefault("node_id", node.get("node_id"))
            payload.setdefault("node_type", node.get("node_type"))
            self.evidence_ledger.evidence_items.append(payload)
            refs.append(evidence_id)
        self.evidence_ledger.quality_checks = {
            **self.evidence_ledger.quality_checks,
            "evidence_count": len(self.evidence_ledger.evidence_items),
            "claim_count": len(self.evidence_ledger.claims),
        }
        return refs

    def commit_claims(self, claims: list[dict[str, Any]]) -> list[str]:
        refs: list[str] = []
        for index, item in enumerate(claims, start=1):
            payload = dict(item)
            claim_id = str(payload.get("claim_id") or f"claim_{len(self.evidence_ledger.claims) + index}")
            payload["claim_id"] = claim_id
            self.evidence_ledger.claims.append(payload)
            refs.append(claim_id)
        self.evidence_ledger.final_claim_ids = [
            str(item.get("claim_id"))
            for item in self.evidence_ledger.claims
            if item.get("claim_id") and item.get("status", "candidate") in {"candidate", "confirmed", "final"}
        ]
        self.evidence_ledger.quality_checks = {
            **self.evidence_ledger.quality_checks,
            "evidence_count": len(self.evidence_ledger.evidence_items),
            "claim_count": len(self.evidence_ledger.claims),
        }
        return refs

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
        }


class RuntimeResult(BaseModel):
    """Final result of a V2 runtime run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "runtime_result.v1"
    status: RuntimeStatus
    node_results: list[NodeResult] = Field(default_factory=list)
    evidence_ledger: EvidenceLedger = Field(default_factory=EvidenceLedger)
    trace: dict[str, Any] = Field(default_factory=dict)
    complete_payload: dict[str, Any] = Field(default_factory=dict)
    cancel_payload: dict[str, Any] | None = None


def build_complete_payload(
    *,
    state: RuntimeState,
    status: RuntimeStatus,
    final_content: str = "",
    cancelled: bool = False,
    cancel_reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "chat_complete",
        "thread_id": state.thread_id,
        "trace_id": state.trace_id,
        "request_id": state.request_id,
        "runtime": "agent_engine_v2",
        "status": status,
        "final_content": final_content,
        "todos": [],
        "event_count": len(state.trace_events),
        "timestamp": _timestamp(),
        "node_results": [item.model_dump(mode="json") for item in state.node_results],
        "evidence_ledger": state.evidence_ledger.model_dump(mode="json"),
        "trace": state.trace_payload(),
    }
    if cancelled:
        payload.update({"cancelled": True, "cancel_reason": cancel_reason or "user_stop", "final_content": ""})
    return payload


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
