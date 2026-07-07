"""Agent Engine V2 empty sidecar engine."""

from __future__ import annotations

from typing import Any

from .contracts import (
    ContextFrame,
    EvidenceLedger,
    ExecutionPlan,
    OutputFrame,
    PlanSnapshotV2,
    SkillRoute,
)
from .understanding import IntentFrameBuilder, RewriteFrameBuilder


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
        snapshot_metadata = dict(metadata or {})
        if thread_id:
            snapshot_metadata["thread_id"] = thread_id
        if request_id:
            snapshot_metadata["request_id"] = request_id
        if auth_context is not None:
            snapshot_metadata["auth_context_present"] = True

        context_frame = ContextFrame()
        intent_frame = IntentFrameBuilder().build(raw_message)
        rewrite_frame = RewriteFrameBuilder().build(
            raw_message,
            intent_frame=intent_frame,
            context_frame=context_frame,
        )

        return PlanSnapshotV2(
            status="not_implemented",
            intent_frame=intent_frame,
            rewrite_frame=rewrite_frame,
            context_frame=context_frame,
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
                "request_understanding": {
                    "raw_message": raw_message,
                    "user_rewrite": rewrite_frame.user_rewrite,
                    "rewrite_reason": rewrite_frame.rewrite_reason,
                    "fallback_used": bool(intent_frame.model_trace.get("fallback_used")),
                },
            },
            warnings=["Agent Engine V2 is not implemented yet."],
            metadata=snapshot_metadata,
        )
