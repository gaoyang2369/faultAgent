"""Plan-only skill routing for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from ..contracts import ContextFrame, EffectiveRequestFrame, IntentFrame, RewriteFrame, SkillRoute
from .loader import SkillLoader
from .registry import SkillRegistry


_DIAGNOSIS_SKILLS = (
    "fault_code_explain",
    "runtime_status",
    "alarm_triage",
    "root_cause",
    "report_generation",
    "workorder_decision",
)


class SkillRouter:
    """Select a minimal skill set from understood request and resolved context."""

    def __init__(
        self,
        *,
        registry: SkillRegistry | None = None,
        loader: SkillLoader | None = None,
    ) -> None:
        self.registry = registry or SkillRegistry()
        self.loader = loader or SkillLoader(self.registry)

    def route(
        self,
        *,
        intent_frame: IntentFrame,
        rewrite_frame: RewriteFrame,
        context_frame: ContextFrame,
        effective_request_frame: EffectiveRequestFrame | None = None,
    ) -> SkillRoute:
        selected = _select_skills(intent_frame, rewrite_frame, effective_request_frame)
        blocked_skills: dict[str, str] = {}
        routing_reason = "规则路由选择命中的 V2 skill。"

        if _needs_clarification(context_frame, intent_frame, effective_request_frame):
            blocked_skills = {
                skill: _clarification_reason(context_frame, intent_frame, effective_request_frame)
                for skill in _DIAGNOSIS_SKILLS
                if skill in selected or skill in ("runtime_status", "alarm_triage", "root_cause", "report_generation", "workorder_decision")
            }
            selected = ["clarification"]
            routing_reason = "上下文存在歧义或缺失，先进入 clarification skill。"

        selected, primary_skill = _resolve_composition(selected)
        loaded_skills = self.loader.load(selected)
        load_set = [
            item
            for skill_name in selected
            for item in loaded_skills.get(skill_name, _EMPTY_LOADED).loaded_files
        ]
        return SkillRoute(
            selected_skills=selected,
            primary_skill=primary_skill,
            skill_inputs=_skill_inputs(intent_frame, rewrite_frame, context_frame, loaded_skills, effective_request_frame),
            load_set=load_set,
            skill_confidence=_confidence(intent_frame, selected, context_frame),
            routing_reason=routing_reason,
            blocked_skills=blocked_skills,
        )


def _select_skills(
    intent_frame: IntentFrame,
    rewrite_frame: RewriteFrame,
    effective_request_frame: EffectiveRequestFrame | None = None,
) -> list[str]:
    if effective_request_frame is not None:
        semantic = effective_request_frame.effective_semantic_intent or effective_request_frame.semantic_intent
        capabilities = {
            goal.capability
            for goal in effective_request_frame.requested_goal_set.goals
            if not effective_request_frame.authorized_goal_ids or goal.goal_id in effective_request_frame.authorized_goal_ids
        }
        text = f"{intent_frame.normalized_message} {rewrite_frame.user_rewrite}"
        selected: list[str] = []
        if any(word in text for word in ("根因", "原因分析", "为什么", "排查")):
            selected.append("root_cause")
        if capabilities.intersection({"explain_fault_code"}) or semantic in {"explain_fault_code", "expand_previous_answer", "show_manual_fields"}:
            selected.append("fault_code_explain")
        if capabilities.intersection({"check_runtime_status", "compare_runtime_status"}) or semantic == "check_runtime_status":
            selected.append("runtime_status")
        if capabilities.intersection({"diagnose_fault", "resolution_recommendation"}) or semantic in {"diagnose_fault", "health_assessment", "diagnose_from_runtime"}:
            selected.append("alarm_triage")
        if semantic == "root_cause_analysis":
            selected.append("root_cause")
        if (
            effective_request_frame.effective_device_refs
            and effective_request_frame.effective_fault_code_refs
            and semantic in {"check_runtime_status", "explain_fault_code", "diagnose_from_runtime"}
        ):
            selected.append("alarm_triage")
        if semantic == "diagnose_from_runtime":
            selected.extend(["alarm_triage", "root_cause"])
        if "generate_report" in capabilities or semantic in {"generate_report", "generate_report_from_previous"}:
            selected.append("report_generation")
        if "generate_report" in set(intent_frame.sub_intents) or "report" in intent_frame.requested_outputs:
            selected.append("report_generation")
        if capabilities.intersection({"create_workorder_draft", "confirm_workorder_draft"}) or semantic in {"decide_workorder", "create_workorder_draft", "confirm_workorder_draft"}:
            selected.append("workorder_decision")
        if set(intent_frame.sub_intents).intersection({"decide_workorder", "create_workorder_draft", "confirm_workorder_draft", "dispatch_workorder"}):
            selected.append("workorder_decision")
        if selected:
            return _dedupe(selected)
    sub_intents = set(intent_frame.sub_intents)
    text = f"{intent_frame.normalized_message} {rewrite_frame.user_rewrite}"
    selected: list[str] = []
    if "explain_fault_code" in sub_intents:
        selected.append("fault_code_explain")
    if "check_current_status" in sub_intents:
        selected.append("runtime_status")
    if intent_frame.device_refs and intent_frame.fault_code_refs and (
        "check_current_status" in sub_intents or "explain_fault_code" in sub_intents
    ):
        selected.append("alarm_triage")
    if any(word in text for word in ("根因", "原因分析", "为什么", "排查")):
        selected.append("root_cause")
    if "generate_report" in sub_intents or "report" in intent_frame.requested_outputs:
        selected.append("report_generation")
    if sub_intents.intersection({"decide_workorder", "create_workorder_draft", "confirm_workorder_draft", "dispatch_workorder"}):
        selected.append("workorder_decision")
    return _dedupe(selected) or ["clarification"]


def _resolve_composition(selected: list[str]) -> tuple[list[str], str]:
    candidates = _dedupe([skill for skill in selected if skill])
    if not candidates:
        return [], ""
    if "clarification" in candidates:
        return ["clarification"], "clarification"

    candidate_set = set(candidates)
    if {"fault_code_explain", "runtime_status"}.issubset(candidate_set):
        candidates.append("alarm_triage")
        candidate_set.add("alarm_triage")

    primary = _default_primary(candidates)
    if {"report_generation", "workorder_decision"}.issubset(candidate_set):
        primary = "report_generation"
    elif {"alarm_triage", "workorder_decision"}.issubset(candidate_set):
        primary = "alarm_triage"
    elif {"fault_code_explain", "runtime_status"}.issubset(candidate_set):
        primary = "alarm_triage"

    ordered = _order_skills(candidates)
    ordered = [primary, *[skill for skill in ordered if skill != primary]]
    if "workorder_decision" in ordered and primary != "workorder_decision":
        ordered = [skill for skill in ordered if skill != "workorder_decision"] + ["workorder_decision"]
    return _dedupe(ordered), primary


def _default_primary(selected: list[str]) -> str:
    ordered = _order_skills(selected)
    return ordered[0] if ordered else ""


def _order_skills(selected: list[str]) -> list[str]:
    priority = {
        "report_generation": 0,
        "workorder_decision": 1,
        "root_cause": 2,
        "alarm_triage": 3,
        "runtime_status": 4,
        "fault_code_explain": 5,
        "clarification": 6,
    }
    return sorted(_dedupe(selected), key=lambda skill: priority.get(skill, 100))


def _needs_clarification(
    context_frame: ContextFrame,
    intent_frame: IntentFrame,
    effective_request_frame: EffectiveRequestFrame | None = None,
) -> bool:
    if effective_request_frame is not None:
        if effective_request_frame.needs_clarification:
            return True
        if _effective_request_has_executable_target(effective_request_frame):
            return False
    if context_frame.relation_to_previous == "ambiguous":
        return True
    if "missing_device" in intent_frame.ambiguities and not (
        effective_request_frame and effective_request_frame.effective_device_refs
    ):
        return True
    if context_frame.missing_context and any(item in intent_frame.ambiguities for item in ("missing_device", "deictic_reference_without_context")):
        return True
    return False


def _clarification_reason(
    context_frame: ContextFrame,
    intent_frame: IntentFrame,
    effective_request_frame: EffectiveRequestFrame | None = None,
) -> str:
    if effective_request_frame is not None and effective_request_frame.clarification_question:
        return effective_request_frame.clarification_question
    if context_frame.missing_context:
        return "；".join(context_frame.missing_context)
    if intent_frame.ambiguities:
        return "；".join(intent_frame.ambiguities)
    return "上下文不足，需要用户澄清。"


def _skill_inputs(
    intent_frame: IntentFrame,
    rewrite_frame: RewriteFrame,
    context_frame: ContextFrame,
    loaded_skills: dict[str, Any],
    effective_request_frame: EffectiveRequestFrame | None = None,
) -> dict[str, Any]:
    device_refs = (
        list(effective_request_frame.effective_device_refs)
        if effective_request_frame is not None
        else list(intent_frame.device_refs)
    )
    fault_code_refs = (
        list(effective_request_frame.effective_fault_code_refs)
        if effective_request_frame is not None
        else list(intent_frame.fault_code_refs)
    )
    requested_outputs = list(intent_frame.requested_outputs)
    if effective_request_frame is not None:
        if effective_request_frame.requested_output_mode == "report" and "report" not in requested_outputs:
            requested_outputs.append("report")
        if effective_request_frame.requested_action in {"decide_workorder", "create_workorder_draft", "confirm_workorder_draft"}:
            requested_outputs.append("workorder_draft")
    return {
        "device_refs": device_refs,
        "fault_code_refs": fault_code_refs,
        "requested_outputs": list(dict.fromkeys(requested_outputs)),
        "requested_output_mode": effective_request_frame.requested_output_mode if effective_request_frame is not None else "",
        "requested_action": effective_request_frame.requested_action if effective_request_frame is not None else "",
        "semantic_intent": effective_request_frame.effective_semantic_intent if effective_request_frame is not None else "",
        "target_artifact_id": effective_request_frame.target_artifact_id if effective_request_frame is not None else None,
        "target_artifact_type": effective_request_frame.target_artifact_type if effective_request_frame is not None else None,
        "target_evidence_bundle_id": effective_request_frame.target_evidence_bundle_id if effective_request_frame is not None else None,
        "target_report_id": effective_request_frame.target_report_id if effective_request_frame is not None else None,
        "stale_evidence_disclosure_required": (
            effective_request_frame.stale_evidence_disclosure_required if effective_request_frame is not None else False
        ),
        "user_rewrite": rewrite_frame.user_rewrite,
        "retrieval_queries": list(rewrite_frame.retrieval_queries),
        "context_relation": context_frame.relation_to_previous,
        "debug_context_relation": context_frame.relation_to_previous,
        "debug_missing_context": list(context_frame.missing_context),
        "debug_reuse_blockers": list(context_frame.reuse_blockers),
        "debug_inherited_slots": dict(context_frame.inherited_slots),
        "effective_request": effective_request_frame.model_dump(mode="json", exclude_none=True) if effective_request_frame is not None else {},
        "loaded_skill_names": list(loaded_skills),
    }


def _confidence(intent_frame: IntentFrame, selected: list[str], context_frame: ContextFrame) -> float:
    if not selected:
        return 0.0
    if selected == ["clarification"]:
        return 0.6
    if context_frame.reuse_blockers:
        return min(intent_frame.confidence, 0.65)
    return max(0.5, intent_frame.confidence)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _effective_request_has_executable_target(frame: EffectiveRequestFrame) -> bool:
    if frame.target_artifact_id:
        return True
    semantic = frame.effective_semantic_intent or frame.semantic_intent
    if semantic in {"explain_fault_code", "expand_previous_answer", "show_manual_fields"}:
        return bool(frame.effective_fault_code_refs)
    if semantic in {"create_workorder_draft", "confirm_workorder_draft", "decide_workorder"}:
        return bool(frame.effective_device_refs)
    if semantic in {"generate_report", "generate_report_from_previous", "check_runtime_status", "diagnose_from_runtime"}:
        return bool(frame.effective_device_refs or frame.effective_fault_code_refs)
    return bool(frame.effective_device_refs or frame.effective_fault_code_refs)


class _EmptyLoaded:
    loaded_files: list[str] = []


_EMPTY_LOADED = _EmptyLoaded()
