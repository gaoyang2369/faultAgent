"""Agent Engine V2 empty sidecar engine."""

from __future__ import annotations

from typing import Any

from .contracts import (
    EvidenceLedger,
    ExecutionPlan,
    OutputFrame,
    PlanSnapshotV2,
)
from .context import ContextFrameAdapter
from .skills import SkillRouter
from .understanding import IntentFrameBuilder, RewriteFrameBuilder


class AgentEngineV2:
    """Plan-only placeholder for the V2 engine.

    Phase 3 still avoids real tools, LLM calls, artifact writes, and legacy
    route integration. The snapshot may include understanding, context, and
    skill-route outputs, but execution planning remains empty.
    """

    def plan_only(
        self,
        *,
        raw_message: str,
        thread_id: str = "",
        request_id: str | None = None,
        auth_context: Any | None = None,
        context_manager: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
        recent_context_signals: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PlanSnapshotV2:
        snapshot_metadata = dict(metadata or {})
        if thread_id:
            snapshot_metadata["thread_id"] = thread_id
        if request_id:
            snapshot_metadata["request_id"] = request_id
        if auth_context is not None:
            snapshot_metadata["auth_context_present"] = True

        intent_frame = IntentFrameBuilder().build(raw_message)
        context_frame = ContextFrameAdapter(context_manager).resolve(
            thread_id=thread_id,
            raw_message=raw_message,
            intent_frame=intent_frame,
            auth_context=auth_context,
            conversation_context=conversation_context,
            recent_context_signals=recent_context_signals,
        )
        rewrite_frame = RewriteFrameBuilder().build(
            raw_message,
            intent_frame=intent_frame,
            context_frame=context_frame,
        )
        skill_route = SkillRouter().route(
            intent_frame=intent_frame,
            rewrite_frame=rewrite_frame,
            context_frame=context_frame,
        )

        return PlanSnapshotV2(
            status="not_implemented",
            intent_frame=intent_frame,
            rewrite_frame=rewrite_frame,
            context_frame=context_frame,
            skill_route=skill_route,
            execution_plan=ExecutionPlan(),
            evidence_ledger=EvidenceLedger(),
            output_frame=OutputFrame(
                guardrail_result={
                    "status": "not_implemented",
                    "reason": "Agent Engine V2 plan_only has no executable runtime in Phase 3.",
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
                "context_resolution": {
                    "relation_to_previous": context_frame.relation_to_previous,
                    "reuse_decision": context_frame.reuse_decision,
                    "missing_context": list(context_frame.missing_context),
                    "reuse_blockers": list(context_frame.reuse_blockers),
                },
                "skill_route": {
                    "primary_skill": skill_route.primary_skill,
                    "selected_skills": list(skill_route.selected_skills),
                    "load_set": list(skill_route.load_set),
                    "blocked_skills": dict(skill_route.blocked_skills),
                },
            },
            warnings=["Agent Engine V2 is not implemented yet."],
            metadata=snapshot_metadata,
        )
