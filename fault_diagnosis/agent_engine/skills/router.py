"""Plan-only skill routing for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from ..contracts import ContextFrame, IntentFrame, RewriteFrame, SkillRoute
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
    ) -> SkillRoute:
        selected = _select_skills(intent_frame, rewrite_frame)
        blocked_skills: dict[str, str] = {}
        routing_reason = "规则路由选择命中的 V2 skill。"

        if _needs_clarification(context_frame, intent_frame):
            blocked_skills = {
                skill: _clarification_reason(context_frame, intent_frame)
                for skill in _DIAGNOSIS_SKILLS
                if skill in selected or skill in ("runtime_status", "alarm_triage", "root_cause", "report_generation", "workorder_decision")
            }
            selected = ["clarification"]
            routing_reason = "上下文存在歧义或缺失，先进入 clarification skill。"

        selected = _dedupe([skill for skill in selected if skill])
        loaded_skills = self.loader.load(selected)
        load_set = [
            item
            for skill_name in selected
            for item in loaded_skills.get(skill_name, _EMPTY_LOADED).loaded_files
        ]
        return SkillRoute(
            selected_skills=selected,
            primary_skill=selected[0] if selected else "",
            skill_inputs=_skill_inputs(intent_frame, rewrite_frame, context_frame, loaded_skills),
            load_set=load_set,
            skill_confidence=_confidence(intent_frame, selected, context_frame),
            routing_reason=routing_reason,
            blocked_skills=blocked_skills,
        )


def _select_skills(intent_frame: IntentFrame, rewrite_frame: RewriteFrame) -> list[str]:
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
    if sub_intents.intersection({"decide_workorder", "create_workorder_draft", "dispatch_workorder"}):
        selected.append("workorder_decision")
    return selected or ["clarification"]


def _needs_clarification(context_frame: ContextFrame, intent_frame: IntentFrame) -> bool:
    if context_frame.relation_to_previous == "ambiguous":
        return True
    if context_frame.missing_context and any(item in intent_frame.ambiguities for item in ("missing_device", "deictic_reference_without_context")):
        return True
    return False


def _clarification_reason(context_frame: ContextFrame, intent_frame: IntentFrame) -> str:
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
) -> dict[str, Any]:
    return {
        "device_refs": list(intent_frame.device_refs),
        "fault_code_refs": list(intent_frame.fault_code_refs),
        "requested_outputs": list(intent_frame.requested_outputs),
        "user_rewrite": rewrite_frame.user_rewrite,
        "retrieval_queries": list(rewrite_frame.retrieval_queries),
        "context_relation": context_frame.relation_to_previous,
        "inherited_slots": dict(context_frame.inherited_slots),
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


class _EmptyLoaded:
    loaded_files: list[str] = []


_EMPTY_LOADED = _EmptyLoaded()
