"""Canonical Goal-to-skill routing."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.canonical_turn import (
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)

from ..contracts import SkillRoute
from .loader import SkillLoader
from .registry import SkillRegistry


_SKILL_BY_CAPABILITY = {
    "explain_fault_code": "fault_code_explain",
    "check_runtime_status": "runtime_status",
    "compare_runtime_status": "runtime_status",
    "diagnose_fault": "alarm_triage",
    "resolution_recommendation": "alarm_triage",
    "generate_report": "report_generation",
    "create_workorder_draft": "workorder_decision",
    "dispatch_workorder": "workorder_decision",
    "evaluate_workorder_need": "workorder_decision",
}


class SkillRouter:
    """Map canonical capabilities to skills without interpreting text or nodes."""

    def __init__(self, *, registry: SkillRegistry | None = None, loader: SkillLoader | None = None) -> None:
        self.registry = registry or SkillRegistry()
        self.loader = loader or SkillLoader(self.registry)

    def route(
        self,
        *,
        request: CanonicalTurnRequest | None = None,
        authorization: list[GoalAuthorizationDecision] | None = None,
        readiness: list[GoalReadinessDecision] | None = None,
        sources: list[GoalSourceResolution] | None = None,
        **legacy: Any,
    ) -> SkillRoute:
        if request is None:
            from ..compat.phase1_planning import legacy_skill_route

            return legacy_skill_route(legacy, registry=self.registry, loader=self.loader)
        if authorization is None or readiness is None or sources is None:
            raise TypeError("per-goal authorization, readiness and source resolution are required")
        auth = {item.goal_id: item for item in authorization}
        ready = {item.goal_id: item for item in readiness}
        source = {item.goal_id: item for item in sources}
        selected: list[str] = []
        blocked: dict[str, str] = {}
        for goal in request.goals:
            skill = _SKILL_BY_CAPABILITY[goal.capability]
            if auth[goal.goal_id].status == "denied":
                blocked[goal.goal_id] = auth[goal.goal_id].reason_code or "permission_denied"
                continue
            if ready[goal.goal_id].status.startswith("blocked_"):
                blocked[goal.goal_id] = ready[goal.goal_id].status
                continue
            if ready[goal.goal_id].status == "satisfied_by_artifact":
                continue
            if skill not in selected:
                selected.append(skill)
        loaded = self.loader.load(selected)
        load_set = [path for skill in selected for path in loaded[skill].loaded_files]
        return SkillRoute(
            selected_skills=selected,
            primary_skill=selected[0] if selected else "",
            skill_inputs={
                "canonical_request_id": request.request_id,
                "goals": [
                    {
                        "goal_id": goal.goal_id,
                        "capability": goal.capability,
                        "resolved_slots": dict(goal.resolved_slots),
                        "source": source[goal.goal_id].model_dump(mode="json"),
                    }
                    for goal in request.goals
                ],
                "loaded_skill_names": list(loaded),
            },
            load_set=load_set,
            skill_confidence=1.0 if selected else 0.0,
            routing_reason="Canonical Goal capability mapping.",
            blocked_skills=blocked,
        )


def skill_for_capability(capability: str) -> str:
    return _SKILL_BY_CAPABILITY.get(capability, "")
