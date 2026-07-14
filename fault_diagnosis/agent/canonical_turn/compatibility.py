"""One-way projection from canonical authority to deprecated debug frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fault_diagnosis.agent.contracts import (
    ContextFrame,
    EffectiveGoal,
    EffectiveRequestFrame,
    GoalQuerySpec,
    IntentFrame,
    RequestedGoalSet,
    RewriteFrame,
    SourceBinding,
    TargetScope,
)
from fault_diagnosis.domain.canonical_turn import (
    BoundCanonicalTurn,
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)


_DELIVERABLES = {
    "explain_fault_code": ["fault_code_explanation"],
    "check_runtime_status": ["runtime_status"],
    "compare_runtime_status": ["runtime_comparison"],
    "diagnose_fault": ["diagnosis"],
    "resolution_recommendation": ["recommendations"],
    "generate_report": ["report"],
    "create_workorder_draft": ["workorder_draft"],
    "dispatch_workorder": ["permission_denied"],
    "evaluate_workorder_need": ["workorder_decision"],
}


@dataclass(frozen=True)
class LegacyFrameProjection:
    intent_frame: IntentFrame
    rewrite_frame: RewriteFrame
    context_frame: ContextFrame
    effective_request_frame: EffectiveRequestFrame
    primary_execution_capability: str
    compatibility_only: bool = True


def project_legacy_frames_from_canonical(
    request: CanonicalTurnRequest,
    authorization: list[GoalAuthorizationDecision],
    readiness: list[GoalReadinessDecision],
    sources: list[GoalSourceResolution],
    *,
    conversation_context: dict[str, Any] | None = None,
    bound_turn: BoundCanonicalTurn | None = None,
) -> LegacyFrameProjection:
    """Project, never infer: this function cannot add, delete or reorder Goals."""

    auth_by_goal = {item.goal_id: item for item in authorization}
    ready_by_goal = {item.goal_id: item for item in readiness}
    source_by_goal = {item.goal_id: item for item in sources}
    devices = _entity_values(request, "device_reference")
    fault_codes = _entity_values(request, "fault_code")
    time_windows = _entity_values(request, "time_window")
    user_goals = [goal for goal in request.goals if goal.user_requested]
    primary = user_goals[0].capability if len(user_goals) == 1 else "composite" if user_goals else ""
    source = next((source_by_goal[goal.goal_id] for goal in request.goals if source_by_goal[goal.goal_id].artifact_id), None)
    context_relation = "report_handoff" if source and any(goal.capability == "generate_report" for goal in request.goals) else "continuation" if source else "new_case"
    if request.pending_binding.kind in {"slot_only", "mixed"}:
        context_relation = "pending_resume"
    candidate_devices = _binding_candidate_devices(bound_turn)
    projected_fault_codes = list(
        dict.fromkeys(
            [
                *fault_codes,
                *(
                    str(value)
                    for item in sources
                    for value in _slot_values(item.resolved_slots.get("fault_code"))
                    if len(_slot_values(item.resolved_slots.get("fault_code"))) == 1
                ),
            ]
        )
    )
    if any(item.status == "blocked_missing_slot" for item in readiness):
        context_relation = "ambiguous"

    effective_goals = [
        EffectiveGoal(
            goal_id=goal.goal_id,
            capability=goal.capability,
            requested_deliverables=list(_DELIVERABLES.get(goal.capability, [])),
            required_slots=list(goal.required_slots),
            source_policy=("reuse_verified_artifact" if source_by_goal[goal.goal_id].artifact_id else "collect_new"),
            depends_on_goal_ids=list(goal.dependencies),
            origin=goal.origin,
            clause_index=goal.clause_index,
            user_requested=goal.user_requested,
            user_visible=goal.user_visible,
            explicit=goal.origin == "explicit",
            confidence=1.0,
        )
        for goal in request.goals
    ]
    source_bindings = [
        SourceBinding(
            goal_id=item.goal_id,
            artifact_id=str(item.artifact_id),
            artifact_type=str(item.artifact_type or ""),
            thread_id=request.thread_id,
            lineage_status="complete",
            persistence_status="committed",
            readback_verified=True,
            source_policy="reuse_verified_artifact",
            selection_reason=item.reason,
        )
        for item in sources
        if item.artifact_id
    ]
    clarification = [
        {
            "goal_id": goal.goal_id,
            "capability": goal.capability,
            "missing_slot": slot,
            "status": ready_by_goal[goal.goal_id].status,
        }
        for goal in request.goals
        for slot in goal.missing_slots
        if ready_by_goal[goal.goal_id].status == "blocked_missing_slot"
    ]
    denied = [
        {
            "goal_id": goal.goal_id,
            "capability": goal.capability,
            "reason": auth_by_goal[goal.goal_id].reason_code or auth_by_goal[goal.goal_id].reason,
        }
        for goal in request.goals
        if auth_by_goal[goal.goal_id].status == "denied"
    ]
    query_specs = [
        GoalQuerySpec(
            query_spec_id=f"query_{goal.goal_id}",
            goal_id=goal.goal_id,
            capability=goal.capability,
            sql_question=request.raw_message if goal.capability in {"check_runtime_status", "compare_runtime_status", "diagnose_fault"} else None,
            rag_query=request.raw_message if goal.capability in {"explain_fault_code", "diagnose_fault", "resolution_recommendation"} else None,
            target_devices=_goal_devices(goal, devices, source_by_goal[goal.goal_id]),
            fault_codes=list(projected_fault_codes),
        )
        for goal in request.goals
    ]
    target_devices = list(dict.fromkeys(device for goal in request.goals for device in _goal_devices(goal, devices, source_by_goal[goal.goal_id])))
    legacy_semantic = (
        "expand_previous_answer"
        if primary == "explain_fault_code" and source is not None and any(marker in request.raw_message for marker in ("详细", "展开", "多说", "字段"))
        else primary
    )
    effective = EffectiveRequestFrame(
        raw_message=request.raw_message,
        normalized_message=request.raw_message.strip(),
        original_semantic_intent=primary,
        effective_semantic_intent=legacy_semantic,
        authorized_semantic_intent=legacy_semantic if not denied else "",
        semantic_intent=legacy_semantic,
        task_family="composite" if primary == "composite" else primary,
        requested_action=primary,
        requested_output_mode="report" if any(goal.capability == "generate_report" for goal in request.goals) else "detailed" if any(marker in request.raw_message for marker in ("详细", "展开", "字段")) else "concise",
        effective_device_refs=target_devices,
        effective_fault_code_refs=projected_fault_codes,
        effective_time_window={"raw": time_windows[0]} if time_windows else next((dict(item.resolved_slots["time_window"]) for item in sources if isinstance(item.resolved_slots.get("time_window"), dict)), {}),
        target_artifact_id=source.artifact_id if source else None,
        target_artifact_type=source.artifact_type if source else None,
        freshness=source.source_freshness if source else "unknown",
        stale_evidence_disclosure_required=bool(source and source.status == "stale"),
        needs_clarification=bool(clarification),
        clarification_question=_binding_question(bound_turn) or _clarification_question(clarification),
        ambiguity=_ambiguity(request, clarification, candidate_devices),
        confidence=1.0,
        resolution_trace=[item.model_dump(mode="json") for item in sources],
        requested_goal_set=RequestedGoalSet(
            goals=effective_goals,
            primary_goal_id=user_goals[0].goal_id if user_goals else "",
        ),
        target_scope=TargetScope(
            operation="compare" if any(goal.capability == "compare_runtime_status" for goal in request.goals) else "replace" if any(entity.kind == "correction_reference" for entity in request.current_parse.entities) else "keep",
            resolved_devices=target_devices,
            included_devices=target_devices,
            excluded_devices=[device for device in list(dict.fromkeys([*devices, *candidate_devices])) if device not in target_devices] if any(entity.kind == "correction_reference" for entity in request.current_parse.entities) else [],
            explicit_switch=any(entity.kind == "correction_reference" for entity in request.current_parse.entities),
            source="current_message" if devices else "artifact" if source else "current_message",
        ),
        goal_query_specs=query_specs,
        requested_goals=[goal.capability for goal in user_goals],
        authorized_goal_ids=[item.goal_id for item in authorization if item.status == "authorized"],
        dropped_goals=denied,
        clarification_reasons=clarification,
        source_bindings=source_bindings,
        authorization_decision={"goals": [item.model_dump(mode="json") for item in authorization]},
    )
    intent = IntentFrame(
        raw_message=request.raw_message,
        normalized_message=request.raw_message.strip(),
        primary_intent=primary,
        sub_intents=[goal.capability for goal in request.goals],
        device_refs=devices,
        fault_code_refs=fault_codes,
        requested_outputs=[item for goal in request.goals for item in _DELIVERABLES.get(goal.capability, [])],
        ambiguities=[item["missing_slot"] for item in clarification],
        confidence=1.0,
        model_trace={"source": "canonical_projection", "compatibility_only": True},
    )
    rewrite = RewriteFrame(
        user_rewrite=request.raw_message,
        rewrite_reason="canonical request compatibility projection",
        retrieval_queries=[request.raw_message] if fault_codes else [],
        sql_question=request.raw_message if target_devices else "",
        manual_query=" ".join(fault_codes),
    )
    context = ContextFrame(
        relation_to_previous=context_relation,
        referenced_artifact_id=source.artifact_id if source else None,
        inherited_slots={"device_refs": target_devices, "fault_code_refs": projected_fault_codes},
        missing_context=[item["missing_slot"] for item in clarification],
        reuse_decision="reuse_verified" if source else "collect_new",
        permission_context={"canonical_request_id": request.request_id},
    )
    return LegacyFrameProjection(intent, rewrite, context, effective, primary)


def _entity_values(request: CanonicalTurnRequest, kind: str) -> list[str]:
    return list(dict.fromkeys(entity.value for entity in request.current_parse.entities if entity.kind == kind))


def _goal_devices(goal, fallback: list[str], source: GoalSourceResolution | None = None) -> list[str]:
    value = goal.resolved_slots.get("device")
    if isinstance(value, list):
        return [str(item) for item in value]
    if value:
        return [str(value)]
    if source is not None:
        source_value = source.resolved_slots.get("device")
        if isinstance(source_value, list):
            return [str(item) for item in source_value]
        if source_value:
            return [str(source_value)]
    return list(fallback)


def _slot_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if value is not None and str(value) else []


def _clarification_question(items: list[dict[str, str]]) -> str:
    if not items:
        return ""
    slots = list(dict.fromkeys(item["missing_slot"] for item in items))
    return "请补充：" + "、".join(slots)


def _ambiguity(request: CanonicalTurnRequest, items: list[dict[str, str]], candidate_devices: list[str]) -> dict[str, Any]:
    if not items:
        status = "consumed" if request.pending_binding.consumes_pending else ""
        return {"pending_status": status} if status else {}
    blocker = next(
        (
            value
            for item in request.goals
            for value in item.required_slots
            if value == items[0]["missing_slot"]
        ),
        items[0]["missing_slot"],
    )
    readiness_slot = items[0]["missing_slot"]
    if readiness_slot == "device" and len(candidate_devices) > 1:
        blocker = "exactly_one_device"
    return {
        "slot": blocker,
        "unresolved_slot": items[0]["missing_slot"],
        "original_goals": [goal.model_dump(mode="json") for goal in request.goals if goal.missing_slots],
        "pending_id": request.pending_binding.pending_id,
        "candidate_values": candidate_devices,
    }


def _binding_candidate_devices(bound_turn: BoundCanonicalTurn | None) -> list[str]:
    return list(dict.fromkeys(
        str(option.asset_ref)
        for binding in (bound_turn.goal_bindings if bound_turn else [])
        for option in (binding.clarification.options if binding.clarification else [])
        if option.asset_ref
    ))


def _binding_question(bound_turn: BoundCanonicalTurn | None) -> str:
    return next((item.clarification.question for item in (bound_turn.goal_bindings if bound_turn else []) if item.clarification), "")
