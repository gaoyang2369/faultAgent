"""Agent Engine V2 snapshot builder."""

from __future__ import annotations

from typing import Any

from .contracts import (
    ContextFrame,
    EvidenceLedger,
    EffectiveRequestFrame,
    OutputFrame,
    PlanSnapshotV2,
    ExecutionPlan,
    SkillRoute,
)
from .context import ContextFrameAdapter
from .context.effective_request import EffectiveRequestBuilder
from .planning import PlanCompiler, PlanValidator
from .skills import SkillRouter
from .understanding import IntentFrameBuilder, RewriteFrameBuilder
from fault_diagnosis.domain.security.capability import authorize_capability_preflight
from fault_diagnosis.domain.security.permissions import build_auth_context


class AgentEngineV2:
    """Build side-effect-free V2 plan snapshots."""

    def build_plan_snapshot(
        self,
        *,
        raw_message: str,
        thread_id: str = "",
        request_id: str | None = None,
        auth_context: Any | None = None,
        context_manager: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
        recent_context_signals: dict[str, Any] | None = None,
        llm_candidate_plan: Any | None = None,
        legacy_plan: Any | None = None,
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
        effective_request_frame = EffectiveRequestBuilder().build(
            raw_message=raw_message,
            intent_frame=intent_frame,
            context_frame=context_frame,
            conversation_context=conversation_context,
            recent_context_signals=recent_context_signals,
        )
        effective_auth = auth_context or build_auth_context(role="guest")
        goal_authorizations: list[dict[str, Any]] = []
        authorized_goal_ids: list[str] = []
        for goal in effective_request_frame.requested_goal_set.goals:
            decision = authorize_capability_preflight(effective_auth, goal.capability)
            dumped = {"goal_id": goal.goal_id, "capability": goal.capability, **decision.model_dump(mode="json")}
            goal_authorizations.append(dumped)
            if decision.allowed:
                authorized_goal_ids.append(goal.goal_id)
            else:
                effective_request_frame.dropped_goals.append(
                    {"goal_id": goal.goal_id, "capability": goal.capability, "reason": decision.denied_reason_code or decision.reason}
                )
        effective_request_frame.authorized_goal_ids = authorized_goal_ids
        primary_authorization = authorize_capability_preflight(
            effective_auth,
            effective_request_frame.original_semantic_intent or effective_request_frame.semantic_intent,
        )
        effective_request_frame.authorization_decision = {
            "goals": goal_authorizations,
            "primary": primary_authorization.model_dump(mode="json"),
        }
        effective_request_frame.authorized_semantic_intent = (
            "check_runtime_status"
            if primary_authorization.mode == "degrade"
            else effective_request_frame.effective_semantic_intent
            if primary_authorization.allowed
            else ""
        )
        if effective_request_frame.requested_goal_set.goals and not authorized_goal_ids:
            return _capability_blocked_snapshot(
                intent_frame=intent_frame,
                context_frame=context_frame,
                effective_request_frame=effective_request_frame,
                authorization={**primary_authorization.model_dump(mode="json"), "goals": goal_authorizations},
                metadata=snapshot_metadata,
            )
        context_frame = _normalize_context_with_effective_request(context_frame, effective_request_frame)
        rewrite_frame = RewriteFrameBuilder().build(
            raw_message,
            intent_frame=intent_frame,
            context_frame=context_frame,
        )
        skill_route = SkillRouter().route(
            intent_frame=intent_frame,
            rewrite_frame=rewrite_frame,
            context_frame=context_frame,
            effective_request_frame=effective_request_frame,
        )
        candidate_plan = PlanCompiler().compile(
            skill_route=skill_route,
            intent_frame=intent_frame,
            context_frame=context_frame,
            effective_request_frame=effective_request_frame,
            llm_candidate_plan=llm_candidate_plan,
        )
        validation = PlanValidator().validate(
            candidate_plan=candidate_plan,
            skill_route=skill_route,
            intent_frame=intent_frame,
            auth_context=auth_context,
        )
        snapshot_status = (
            "blocked"
            if validation.status == "blocked"
            else "validated_degraded"
            if validation.status == "degraded"
            else "validated"
        )
        execution_capability = "" if validation.status == "blocked" else _execution_capability(validation.validated_plan)
        validation.validated_plan.execution_capability = execution_capability
        effective_request_frame.executed_semantic_intent = execution_capability

        return PlanSnapshotV2(
            status=snapshot_status,
            intent_frame=intent_frame,
            rewrite_frame=rewrite_frame,
            context_frame=context_frame,
            effective_request_frame=effective_request_frame,
            skill_route=skill_route,
            execution_plan=validation.validated_plan,
            evidence_ledger=EvidenceLedger(),
            output_frame=OutputFrame(
                guardrail_result={
                    "status": validation.status,
                    "issues": [issue.model_dump(mode="json") for issue in validation.issues],
                    "removed_tools": list(validation.removed_tools),
                    "authorization": dict(validation.authorization),
                }
            ),
            trace={
                "engine": "agent_engine_v2",
                "mode": "build_plan_snapshot",
                "status": snapshot_status,
                "requested_goals": list(effective_request_frame.requested_goals),
                "effective_goals": [goal.model_dump(mode="json") for goal in effective_request_frame.requested_goal_set.goals],
                "authorized_goals": list(effective_request_frame.authorized_goal_ids),
                "dropped_goals": list(effective_request_frame.dropped_goals),
                "target_operation": effective_request_frame.target_scope.operation,
                "included_devices": list(effective_request_frame.target_scope.included_devices),
                "excluded_devices": list(effective_request_frame.target_scope.excluded_devices),
                "resolved_devices": list(effective_request_frame.target_scope.resolved_devices),
                "discarded_artifact_ids": list(effective_request_frame.discarded_artifact_ids),
                "goal_query_specs": [item.model_dump(mode="json") for item in effective_request_frame.goal_query_specs],
                "goal_node_mapping": {
                    goal.goal_id: [node.node_id for node in validation.validated_plan.nodes if goal.goal_id in node.goal_ids]
                    for goal in effective_request_frame.requested_goal_set.goals
                },
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
                    "effective_request": effective_request_frame.model_dump(mode="json", exclude_none=True),
                },
                "skill_route": {
                    "primary_skill": skill_route.primary_skill,
                    "selected_skills": list(skill_route.selected_skills),
                    "load_set": list(skill_route.load_set),
                    "blocked_skills": dict(skill_route.blocked_skills),
                },
                "candidate_plan": candidate_plan.model_dump(mode="json"),
                "validation": {
                    "status": validation.status,
                    "issues": [issue.model_dump(mode="json") for issue in validation.issues],
                    "removed_tools": list(validation.removed_tools),
                    "approval_requirements": list(validation.approval_requirements),
                    "authorization": dict(validation.authorization),
                },
            },
            warnings=[issue.message for issue in validation.issues],
            metadata=snapshot_metadata,
        )

    def plan_only(self, **kwargs: Any) -> PlanSnapshotV2:
        """Deprecated compatibility wrapper; use build_plan_snapshot()."""

        return self.build_plan_snapshot(**kwargs)


def _normalize_context_with_effective_request(
    context_frame: ContextFrame,
    effective_request_frame: EffectiveRequestFrame,
) -> ContextFrame:
    """Let the finalized effective request settle legacy ambiguous follow-ups."""

    if context_frame.relation_to_previous != "ambiguous":
        return context_frame
    if effective_request_frame.needs_clarification:
        return context_frame
    fault_codes = [code for code in effective_request_frame.effective_fault_code_refs if str(code).strip()]
    if (
        len(fault_codes) == 1
        and effective_request_frame.target_artifact_id
        and effective_request_frame.semantic_intent
        in {"expand_previous_answer", "show_manual_fields", "explain_fault_code"}
    ):
        return context_frame.model_copy(
            update={
                "relation_to_previous": "knowledge_followup",
                "reuse_decision": "reuse_artifact",
                "missing_context": [],
                "reuse_blockers": [],
            }
        )
    return context_frame


def _capability_blocked_snapshot(
    *,
    intent_frame,
    context_frame,
    effective_request_frame,
    authorization: dict[str, Any],
    metadata: dict[str, Any],
) -> PlanSnapshotV2:
    capability = effective_request_frame.original_semantic_intent or effective_request_frame.semantic_intent
    skill = _skill_for_capability(capability)
    route = SkillRoute(
        selected_skills=[skill] if skill else [],
        primary_skill=skill,
        routing_reason="Capability preflight denied before clarification and runtime planning.",
        blocked_skills={skill: authorization.get("denied_reason_code", "capability_permission_denied")} if skill else {},
    )
    plan = ExecutionPlan(
        plan_id="terminal_capability_denied",
        plan_version="v2.capability_preflight.blocked",
        nodes=[],
        allowed_tools=[],
        execution_capability="",
    )
    message = str(authorization.get("user_message") or authorization.get("reason") or "当前身份无权执行该能力。")
    guardrail = {
        "status": "blocked",
        "issues": [{"code": authorization.get("denied_reason_code"), "severity": "error", "message": message}],
        "authorization": authorization,
        "runtime_invoked": False,
    }
    return PlanSnapshotV2(
        status="blocked",
        intent_frame=intent_frame,
        context_frame=context_frame,
        effective_request_frame=effective_request_frame,
        skill_route=route,
        execution_plan=plan,
        output_frame=OutputFrame(answer_variant="permission_denied", final_answer=message, guardrail_result=guardrail),
        trace={
            "engine": "agent_engine_v2",
            "mode": "build_plan_snapshot",
            "status": "blocked",
            "capability_preflight": authorization,
            "context_resolution": {
                "relation_to_previous": context_frame.relation_to_previous,
                "effective_request": effective_request_frame.model_dump(mode="json", exclude_none=True),
            },
            "skill_route": {
                "primary_skill": route.primary_skill,
                "selected_skills": list(route.selected_skills),
                "blocked_skills": dict(route.blocked_skills),
            },
            "candidate_plan": plan.model_dump(mode="json"),
            "validation": {"status": "blocked", "issues": guardrail["issues"], "authorization": authorization},
        },
        warnings=[message],
        metadata=metadata,
    )


def _skill_for_capability(capability: str) -> str:
    if capability in {"generate_report", "generate_report_from_previous"}:
        return "report_generation"
    if capability in {"decide_workorder", "create_workorder_draft", "confirm_workorder_draft"}:
        return "workorder_decision"
    if capability == "root_cause_analysis":
        return "root_cause"
    if capability in {"diagnose_fault", "diagnose_from_runtime", "health_assessment"}:
        return "alarm_triage"
    if capability == "check_runtime_status":
        return "runtime_status"
    return "fault_code_explain" if capability in {"explain_fault_code", "expand_previous_answer", "show_manual_fields"} else ""


def _execution_capability(plan: ExecutionPlan) -> str:
    if not plan.nodes:
        return ""
    node_types = {node.node_type for node in plan.nodes}
    if "workorder" in node_types:
        return "create_workorder_draft"
    if "report" in node_types:
        return "generate_report"
    if "analysis" in node_types:
        return "diagnose_fault"
    if "sql" in node_types:
        return "check_runtime_status"
    if "rag" in node_types:
        return "explain_fault_code"
    if "clarification" in node_types:
        return "clarify_target"
    return ""
