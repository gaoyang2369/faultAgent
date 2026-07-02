"""Side-effect-free goal-native workflow planning."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..context import summarize_resolved_context
from ..diagnosis.steps import build_request_from_payload
from ..security.contracts import AuthContext
from ..security.policy_engine import apply_authorization_to_decision, authorize_workflow
from .context import ContextManager
from .intent import decide_capabilities, fallback_understanding_payload
from .planning import (
    build_diagnosis_readiness,
    build_manual_confirmation_requirement,
    build_workorder_action_readiness,
    summarize_diagnosis_readiness,
    summarize_manual_confirmation_requirement,
    summarize_workorder_action_readiness,
)
from .workflow import summarize_goal_set


PLAN_SNAPSHOT_SCHEMA_VERSION = "agent_plan_snapshot.v2"


class PlanSnapshot(BaseModel):
    """Structured, side-effect-free plan output for regression evaluation."""

    schema_version: str = PLAN_SNAPSHOT_SCHEMA_VERSION
    resolved_context: dict[str, Any] = Field(default_factory=dict)
    goal_set: dict[str, Any] = Field(default_factory=dict)
    task_family: str = "diagnosis"
    task_family_reason: str = ""
    task_family_source: str = "goal_hint_fallback"
    policy_id: str = ""
    workflow_policy: dict[str, Any] = Field(default_factory=dict)
    enabled_nodes: dict[str, bool] = Field(default_factory=dict)
    skipped_nodes: dict[str, bool] = Field(default_factory=dict)
    planned_tools: list[str] = Field(default_factory=list)
    runtime_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    requested_output: str = "answer"
    readiness: dict[str, Any] = Field(default_factory=dict)
    manual_confirmation: dict[str, Any] = Field(default_factory=dict)
    goals: list[dict[str, Any]] = Field(default_factory=list)
    workflow_route: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    evidence_gaps: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    skip_reasons: dict[str, str] = Field(default_factory=dict)
    plan_mode: str = "normal"
    context_relation: str = "new_task"
    referenced_artifact: dict[str, Any] = Field(default_factory=dict)
    node_reasons: dict[str, str] = Field(default_factory=dict)
    planned_tool_arguments_preview: dict[str, Any] = Field(default_factory=dict)
    blocked_reason: str = ""
    authorization: dict[str, Any] = Field(default_factory=dict)


def build_plan_snapshot(
    *,
    message: str,
    thread_id: str,
    user_identity: str,
    auth_context: AuthContext,
) -> PlanSnapshot:
    """Build a deterministic goal-native plan snapshot without side effects."""

    normalized_message = (message or "").strip()
    context_manager = ContextManager()
    conversation_state = context_manager.load_state(thread_id)
    payload = fallback_understanding_payload(normalized_message, user_identity)
    resolved_context = context_manager.resolve(
        thread_id=thread_id,
        message=normalized_message,
        auth_context=auth_context,
        current_payload=payload,
        state=conversation_state,
    )
    report_from_previous_artifact = resolved_context.relation_to_previous == "report_handoff"
    if report_from_previous_artifact:
        payload["needs_report"] = True
    request = build_request_from_payload(
        normalized_message,
        user_identity,
        payload,
        needs_report=None,
        report_format=str(payload.get("report_format") or "markdown"),
    )
    decision = decide_capabilities(
        payload=payload,
        request=request,
        message=normalized_message,
        report_from_previous_artifact=report_from_previous_artifact,
        conversation_state=conversation_state,
        resolved_context=resolved_context,
    )
    authorization = authorize_workflow(auth_context, decision)
    decision = apply_authorization_to_decision(decision, authorization)
    diagnosis = summarize_diagnosis_readiness(build_diagnosis_readiness(decision=decision))
    workorder = summarize_workorder_action_readiness(build_workorder_action_readiness(decision=decision))
    manual = summarize_manual_confirmation_requirement(
        build_manual_confirmation_requirement(
            decision=decision,
            workorder_action_readiness=workorder,
        )
    )
    decision.diagnosis_readiness = diagnosis
    decision.workorder_action_readiness = workorder
    decision.manual_confirmation = manual

    auth_payload = authorization.model_dump()
    skip_reasons = _skip_reasons(decision, auth_payload)
    node_reasons = _node_reasons(decision, skip_reasons)
    resolved_context_summary = summarize_resolved_context(decision.resolved_context)
    resolved_context_summary["thread_id"] = thread_id
    goal_set_summary = summarize_goal_set(decision.goal_set)
    enabled_nodes = {key: bool(value) for key, value in (decision.enabled_nodes or {}).items() if value}
    skipped_nodes = {key: False for key, value in (decision.enabled_nodes or {}).items() if not value}
    route_payload = _workflow_route_payload(decision, goal_set_summary)

    return PlanSnapshot(
        resolved_context=resolved_context_summary,
        goal_set=goal_set_summary,
        task_family=decision.task_family,
        task_family_reason=decision.task_family_reason,
        task_family_source=decision.task_family_source,
        policy_id=str((decision.workflow_policy or {}).get("policy_id") or ""),
        workflow_policy=dict(decision.workflow_policy or {}),
        enabled_nodes=enabled_nodes,
        skipped_nodes=skipped_nodes,
        planned_tools=list(decision.runtime_tools or []),
        runtime_tools=list(decision.runtime_tools or []),
        forbidden_tools=list((decision.workflow_policy or {}).get("forbidden_tools") or []),
        requested_output=decision.requested_output,
        readiness={"diagnosis": diagnosis, "workorder_action": workorder},
        manual_confirmation=manual,
        goals=_compact_goals(decision.goals),
        workflow_route=route_payload,
        missing_slots=list(decision.missing_slots or []),
        evidence_gaps={
            "required_evidence": decision.required_evidence,
            "satisfied_evidence": decision.satisfied_evidence,
            "missing_or_stale_evidence": decision.missing_or_stale_evidence,
            "should_refresh_runtime_data": decision.should_refresh_runtime_data,
            "evidence_mode": decision.evidence_mode,
        },
        confidence=float(decision.route_confidence or 0.0),
        skip_reasons=skip_reasons,
        plan_mode=decision.plan_mode,
        context_relation=decision.relation_to_previous,
        referenced_artifact=_referenced_artifact_payload(decision),
        node_reasons=node_reasons,
        planned_tool_arguments_preview=_planned_tool_arguments_preview(decision),
        blocked_reason=_blocked_reason(decision, auth_payload),
        authorization=auth_payload,
    )


def _workflow_route_payload(decision: Any, goal_set_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_family": decision.task_family,
        "task_family_reason": decision.task_family_reason,
        "task_family_source": decision.task_family_source,
        "goal_set": goal_set_summary,
        "relation_to_previous": decision.relation_to_previous,
        "plan_mode": decision.plan_mode,
        "evidence_mode": decision.evidence_mode,
        "action_target": decision.action_target,
        "objects": decision.objects,
        "time_window": decision.time_window,
        "subgoals": decision.subgoals,
        "flags": decision.flags,
        "readiness": {
            "diagnosis": decision.diagnosis_readiness,
            "workorder_action": decision.workorder_action_readiness,
        },
        "manual_confirmation": decision.manual_confirmation,
    }


def _referenced_artifact_payload(decision: Any) -> dict[str, Any]:
    if not getattr(decision, "referenced_artifact_id", None):
        return {}
    return {
        "artifact_id": decision.referenced_artifact_id,
        "case_id": decision.referenced_case_id,
        "relation": decision.relation_to_previous,
        "evidence_mode": decision.evidence_mode,
    }


def _compact_goals(goals: Any) -> list[dict[str, Any]]:
    if not isinstance(goals, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in goals:
        data = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item or {}) if isinstance(item, dict) else {}
        if data:
            compact.append(
                {
                    "goal_id": data.get("goal_id"),
                    "goal_type": data.get("goal_type"),
                    "status": data.get("status"),
                    "depends_on": list(data.get("depends_on") or []),
                    "missing_slots": list(data.get("missing_slots") or []),
                    "context_refs": list(data.get("context_refs") or []),
                    "expected_output": data.get("expected_output"),
                    "risk_level": data.get("risk_level"),
                }
            )
    return compact


def _skip_reasons(decision: Any, authorization: dict[str, Any]) -> dict[str, str]:
    denied_nodes = authorization.get("denied_nodes") if isinstance(authorization, dict) else {}
    reasons: dict[str, str] = {}
    for node, enabled in dict(getattr(decision, "enabled_nodes", {}) or {}).items():
        if enabled:
            continue
        if isinstance(denied_nodes, dict) and denied_nodes.get(node):
            reasons[node] = str(denied_nodes[node])
        elif node == "sql":
            reasons[node] = "no_runtime_data_needed_or_missing_device_context"
        elif node == "knowledge":
            reasons[node] = "no_knowledge_lookup_needed"
        elif node == "report":
            reasons[node] = "report_not_requested_or_not_authorized"
        elif node == "workorder_decision":
            reasons[node] = "workorder_not_requested_or_missing_evidence"
        else:
            reasons[node] = "node_not_required_by_policy"
    return reasons


def _node_reasons(decision: Any, skip_reasons: dict[str, str]) -> dict[str, str]:
    reasons = dict(skip_reasons)
    for node, enabled in dict(getattr(decision, "enabled_nodes", {}) or {}).items():
        if enabled:
            reasons[node] = "enabled_by_goal_native_policy"
    return reasons


def _planned_tool_arguments_preview(decision: Any) -> dict[str, Any]:
    previews: dict[str, Any] = {}
    tools = set(getattr(decision, "runtime_tools", []) or [])
    objects = dict(getattr(decision, "objects", {}) or {})
    if "sql_db_query_checker" in tools:
        previews["sql_db_query_checker"] = {
            "query": "<generated during SQL stage; omitted in plan-only>",
            "objects": objects,
        }
    if "sql_db_query" in tools:
        previews["sql_db_query"] = {
            "query": "<generated and ACL-checked during SQL stage; omitted in plan-only>",
            "objects": objects,
        }
    if "query_knowledge_base" in tools:
        previews["query_knowledge_base"] = {
            "query": "<built from user goal, fault codes, and SQL summary during knowledge stage>",
            "fault_codes": objects.get("alarm_codes", []),
            "topics": objects.get("topics", []),
        }
    if "save_report" in tools:
        previews["save_report"] = {
            "report_format": getattr(decision, "requested_output", "answer"),
            "source": "current_or_referenced_evidence_bundle",
        }
    return previews


def _blocked_reason(decision: Any, authorization: dict[str, Any]) -> str:
    if not authorization.get("allowed", True):
        return str(authorization.get("reason") or authorization.get("denied_reason_code") or "authorization_denied")
    missing = list(getattr(decision, "missing_slots", []) or [])
    if missing:
        return f"missing_slots:{','.join(missing)}"
    stale = list(getattr(decision, "missing_or_stale_evidence", []) or [])
    if stale:
        return f"missing_or_stale_evidence:{','.join(stale)}"
    return ""
