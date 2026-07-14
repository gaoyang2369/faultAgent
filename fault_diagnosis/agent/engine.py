"""Canonical-turn plan snapshot facade used by the production runtime."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context

from .canonical_turn import ConversationTurnCoordinator
from .canonical_turn.compatibility import project_legacy_frames_from_canonical
from .contracts import EvidenceLedger, OutputFrame, PlanSnapshotV2
from .planning import PlanCompiler, PlanValidator
from .skills import SkillRouter


class AgentEngineV2:
    """Compatibility facade; all planning authority starts at CanonicalTurnRequest."""

    def build_plan_snapshot(
        self,
        *,
        raw_message: str,
        thread_id: str = "",
        request_id: str | None = None,
        auth_context: Any | None = None,
        context_manager: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
        recent_context_signals: dict[str, Any] | None = None,  # noqa: ARG002 - deprecated compatibility input.
        llm_candidate_plan: Any | None = None,  # noqa: ARG002 - canonical compiler never accepts plan authority.
        legacy_plan: Any | None = None,  # noqa: ARG002 - retained only at the public facade boundary.
        metadata: dict[str, Any] | None = None,
        canonical_result=None,
    ) -> PlanSnapshotV2:
        auth = auth_context or build_auth_context(role="guest")
        context = _conversation_context(conversation_context, context_manager, thread_id)
        turn_key = str(request_id or f"plan:{thread_id}:{raw_message}")
        result = canonical_result or ConversationTurnCoordinator().preview_turn(
            TurnCommand(
                command="preview",
                thread_id=thread_id or "thread.preview",
                user_id=auth.user_id,
                turn_id=turn_key,
                message_id=turn_key,
                idempotency_key=turn_key,
                raw_message=raw_message,
                channel="plan",
            ),
            auth_context=auth,
            conversation_context=context,
        )
        request = result.request
        route = SkillRouter().route(
            request=request,
            authorization=result.authorization,
            readiness=result.readiness,
            sources=result.source_resolutions,
        )
        candidate = PlanCompiler().compile(
            request=request,
            authorization=result.authorization,
            readiness=result.readiness,
            sources=result.source_resolutions,
        )
        validation = PlanValidator().validate(
            candidate_plan=candidate,
            request=request,
            authorization=result.authorization,
            readiness=result.readiness,
            sources=result.source_resolutions,
        )
        projection = project_legacy_frames_from_canonical(
            request,
            result.authorization,
            result.readiness,
            result.source_resolutions,
            conversation_context=context,
        )
        denied = [item for item in result.authorization if item.status == "denied"]
        executable = bool(validation.validated_plan.nodes)
        all_terminal_without_execution = bool(request.goals) and not executable
        has_blocked_goal = any(item.status.startswith("blocked_") for item in result.readiness)
        blocked = validation.status == "blocked" or (all_terminal_without_execution and (bool(denied) or has_blocked_goal))
        status = "blocked" if blocked else "validated"
        issues = [item.model_dump(mode="json") for item in validation.issues]
        issues.extend(
            {
                "code": item.reason_code or "permission_denied",
                "severity": "error",
                "message": item.reason or "Goal authorization denied.",
                "goal_id": item.goal_id,
            }
            for item in denied
        )
        authorization_payload = _authorization_payload(result.authorization)
        output = OutputFrame(
            answer_variant="permission_denied" if denied and not executable else "clarification" if all_terminal_without_execution else "not_implemented",
            final_answer=(denied[0].reason if denied and not executable else projection.effective_request_frame.clarification_question if all_terminal_without_execution else ""),
            guardrail_result={
                "status": status,
                "issues": issues,
                "authorization": authorization_payload,
                "runtime_invoked": False,
            },
        )
        canonical_metadata = {
            **dict(metadata or {}),
            "thread_id": request.thread_id,
            "request_id": request.turn_id,
            "compatibility_only": True,
            "primary_execution_capability": projection.primary_execution_capability,
            "canonical_request": request.model_dump(mode="json"),
            "goal_authorization": [item.model_dump(mode="json") for item in result.authorization],
            "goal_readiness": [item.model_dump(mode="json") for item in result.readiness],
            "goal_source_resolution": [item.model_dump(mode="json") for item in result.source_resolutions],
            "pending_transition": result.pending_transition.model_dump(mode="json"),
        }
        return PlanSnapshotV2(
            status=status,
            intent_frame=projection.intent_frame,
            rewrite_frame=projection.rewrite_frame,
            context_frame=projection.context_frame,
            effective_request_frame=projection.effective_request_frame,
            skill_route=route,
            execution_plan=validation.validated_plan,
            evidence_ledger=EvidenceLedger(),
            output_frame=output,
            trace={
                "engine": "agent_engine_v2",
                "mode": "canonical_turn",
                "status": status,
                "canonical_request": request.model_dump(mode="json"),
                "goal_authorization": canonical_metadata["goal_authorization"],
                "goal_readiness": canonical_metadata["goal_readiness"],
                "goal_source_resolution": canonical_metadata["goal_source_resolution"],
                "requested_goals": [goal.capability for goal in request.goals if goal.user_requested],
                "effective_goals": [goal.model_dump(mode="json") for goal in request.goals],
                "authorized_goals": [item.goal_id for item in result.authorization if item.status == "authorized"],
                "dropped_goals": projection.effective_request_frame.dropped_goals,
                "goal_node_mapping": {
                    goal.goal_id: [node.node_id for node in validation.validated_plan.nodes if goal.goal_id in node.goal_ids]
                    for goal in request.goals
                },
                "request_understanding": {
                    "raw_message": request.raw_message,
                    "source": "canonical_current_utterance_parse",
                    "user_rewrite": projection.rewrite_frame.user_rewrite,
                    "rewrite_reason": projection.rewrite_frame.rewrite_reason,
                    "compatibility_only": True,
                },
                "context_resolution": {
                    "relation_to_previous": projection.context_frame.relation_to_previous,
                    "resolution_status": "ambiguous" if projection.context_frame.relation_to_previous == "ambiguous" else "resolved" if executable else "unresolved",
                    "selected_devices": list(projection.effective_request_frame.effective_device_refs),
                    "selected_artifact_id": projection.effective_request_frame.target_artifact_id,
                    "selected_artifact_type": projection.effective_request_frame.target_artifact_type,
                    "effective_request": projection.effective_request_frame.model_dump(mode="json", exclude_none=True),
                },
                "skill_route": {
                    "selected_skills": list(route.selected_skills),
                    "blocked_skills": dict(route.blocked_skills),
                },
                "candidate_plan": candidate.model_dump(mode="json"),
                "validation": {
                    "status": validation.status,
                    "issues": issues,
                    "authorization": authorization_payload,
                },
            },
            warnings=[str(item.get("message") or "") for item in issues],
            metadata=canonical_metadata,
        )

    def plan_only(self, **kwargs: Any) -> PlanSnapshotV2:
        return self.build_plan_snapshot(**kwargs)


def _conversation_context(value: dict[str, Any] | None, manager: Any, thread_id: str) -> dict[str, Any]:
    context = dict(value or {})
    if manager is None:
        return context
    try:
        state = manager.load_state(thread_id)
        active = state.active_case
    except Exception:
        active = None
    if active is not None and not context.get("artifact_manifests"):
        context["artifact_manifests"] = list(active.artifact_manifests)
    return context


def _authorization_payload(decisions) -> dict[str, Any]:
    denied = next((item for item in decisions if item.status == "denied"), None)
    if denied is None:
        return {"allowed": True, "mode": "allow", "goals": [item.model_dump(mode="json") for item in decisions]}
    return {
        "allowed": False,
        "mode": "deny",
        "denied_reason_code": denied.reason_code,
        "reason": denied.reason,
        "user_message": denied.reason,
        "goals": [item.model_dump(mode="json") for item in decisions],
    }
