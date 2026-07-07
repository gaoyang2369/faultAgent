"""Agent Engine V2 empty sidecar engine."""

from __future__ import annotations

from typing import Any

from .contracts import (
    ContextFrame,
    EvidenceLedger,
    ExecutionPlan,
    IntentFrame,
    OutputFrame,
    PlanSnapshotV2,
    RewriteFrame,
    SkillRoute,
)


class AgentEngineV2:
    """Plan-only placeholder for the V2 engine.

    Phase 1 deliberately avoids real tools, LLM calls, artifact writes, and
    legacy route integration. Later phases can replace this empty snapshot
    assembly with understanding, routing, validation, and runtime steps.
    """

    def plan_only(
        self,
        *,
        raw_message: str,
        thread_id: str = "",
        request_id: str | None = None,
        auth_context: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PlanSnapshotV2:
        normalized_message = (raw_message or "").strip()
        snapshot_metadata = dict(metadata or {})
        if thread_id:
            snapshot_metadata["thread_id"] = thread_id
        if request_id:
            snapshot_metadata["request_id"] = request_id
        if auth_context is not None:
            snapshot_metadata["auth_context_present"] = True

        return PlanSnapshotV2(
            status="not_implemented",
            intent_frame=IntentFrame(
                raw_message=raw_message,
                normalized_message=normalized_message,
            ),
            rewrite_frame=RewriteFrame(),
            context_frame=ContextFrame(),
            skill_route=SkillRoute(),
            execution_plan=ExecutionPlan(),
            evidence_ledger=EvidenceLedger(),
            output_frame=OutputFrame(
                guardrail_result={
                    "status": "not_implemented",
                    "reason": "Agent Engine V2 plan_only is a Phase 1 placeholder.",
                }
            ),
            trace={
                "engine": "agent_engine_v2",
                "mode": "plan_only",
                "status": "not_implemented",
            },
            warnings=["Agent Engine V2 is not implemented yet."],
            metadata=snapshot_metadata,
        )
