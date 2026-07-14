"""Non-production adapters for pre-Phase-2 component unit tests.

Production entrypoints never call these helpers.  They keep the public component
objects import-compatible while canonical authority is exercised by all real turns.
"""

from __future__ import annotations

from typing import Any

from fault_diagnosis.agent.contracts import SkillRoute
from fault_diagnosis.agent.skills.loader import SkillLoader
from fault_diagnosis.agent.skills.registry import SkillRegistry


def legacy_skill_route(values: dict[str, Any], *, registry: SkillRegistry, loader: SkillLoader) -> SkillRoute:
    intent = values.get("intent_frame")
    rewrite = values.get("rewrite_frame")
    context = values.get("context_frame")
    effective = values.get("effective_request_frame")
    if intent is None or context is None:
        raise TypeError("canonical request and per-goal decisions are required")
    capabilities = []
    if effective is not None:
        capabilities = [goal.capability for goal in effective.requested_goal_set.goals]
    sub = set(getattr(intent, "sub_intents", []) or [])
    mapping = {
        "explain_fault_code": "fault_code_explain",
        "check_current_status": "runtime_status",
        "check_runtime_status": "runtime_status",
        "generate_report": "report_generation",
        "decide_workorder": "workorder_decision",
        "create_workorder_draft": "workorder_decision",
        "confirm_workorder_draft": "workorder_decision",
    }
    selected: list[str] = []
    for capability in [*capabilities, *sub]:
        skill = {
            "diagnose_fault": "alarm_triage",
            "resolution_recommendation": "alarm_triage",
            **mapping,
        }.get(capability)
        if skill and skill not in selected:
            selected.append(skill)
    text = f"{getattr(intent, 'normalized_message', '')} {getattr(rewrite, 'user_rewrite', '')}"
    if any(word in text for word in ("根因", "为什么", "原因分析", "排查")) and "root_cause" not in selected:
        selected.append("root_cause")
    if "report" in set(getattr(intent, "requested_outputs", []) or []) and "report_generation" not in selected:
        selected.append("report_generation")
    if getattr(intent, "device_refs", []) and getattr(intent, "fault_code_refs", []) and {"fault_code_explain", "runtime_status"}.issubset(selected):
        selected.append("alarm_triage")
    if getattr(context, "relation_to_previous", "") in {"ambiguous", "unresolved"} or bool(getattr(effective, "needs_clarification", False)):
        selected = ["clarification"]
    if not selected:
        selected = ["clarification"]
    priority = {"report_generation": 0, "workorder_decision": 1, "root_cause": 2, "alarm_triage": 3, "runtime_status": 4, "fault_code_explain": 5, "clarification": 6}
    primary = min(selected, key=lambda item: priority.get(item, 100))
    if {"fault_code_explain", "runtime_status"}.issubset(selected):
        primary = "alarm_triage"
    ordered = [primary, *[item for item in sorted(dict.fromkeys(selected), key=lambda item: priority.get(item, 100)) if item != primary]]
    if "workorder_decision" in ordered and primary != "workorder_decision":
        ordered = [item for item in ordered if item != "workorder_decision"] + ["workorder_decision"]
    loaded = loader.load(ordered)
    devices = list(getattr(effective, "effective_device_refs", []) or getattr(intent, "device_refs", []) or [])
    codes = list(getattr(effective, "effective_fault_code_refs", []) or getattr(intent, "fault_code_refs", []) or [])
    return SkillRoute(
        selected_skills=ordered,
        primary_skill=primary,
        skill_inputs={
            "device_refs": devices,
            "fault_code_refs": codes,
            "requested_outputs": list(getattr(intent, "requested_outputs", []) or []),
            "user_rewrite": str(getattr(rewrite, "user_rewrite", "")),
            "context_relation": str(getattr(context, "relation_to_previous", "")),
            "loaded_skill_names": list(loaded),
        },
        load_set=[path for name in ordered for path in loaded[name].loaded_files],
        skill_confidence=max(0.5, float(getattr(intent, "confidence", 0.5) or 0.5)),
        routing_reason="Pre-Phase-2 unit compatibility adapter.",
        blocked_skills={
            "runtime_status": "ambiguous",
            "alarm_triage": "ambiguous",
            "workorder_decision": "ambiguous",
            "report_generation": "ambiguous",
            "root_cause": "ambiguous",
        } if ordered == ["clarification"] else {},
    )


def validate_legacy_candidate(values: dict[str, Any], result_type, issue_type):
    candidate = values["candidate_plan"]
    validated = candidate.model_copy(deep=True)
    issues = []
    removed = []
    forbidden = {"sql.write", "config.write", "device_control.write", "workorder.dispatch"}
    for tool in list(validated.allowed_tools):
        if tool in forbidden:
            removed.append(tool)
            issues.append(issue_type(code="forbidden_tool_requested", severity="error", message=f"Forbidden tool requested: {tool}", tool=tool))
    validated.allowed_tools = [tool for tool in validated.allowed_tools if tool not in forbidden]
    for node in validated.nodes:
        node.required_tools = [tool for tool in node.required_tools if tool not in forbidden]
        if values.get("require_runtime_inputs") and node.node_type == "sql" and not str(node.inputs.get("sql_query") or ""):
            issues.append(issue_type(code="missing_sql_query", severity="error", message="SQL node requires sql_query before runtime.", node_id=node.node_id))
    status = "blocked" if any(item.severity == "error" for item in issues) else "validated"
    if status == "blocked" and not validated.plan_version.endswith(".blocked"):
        validated.plan_version += ".blocked"
    elif status == "validated" and ".validated" not in validated.plan_version:
        validated.plan_version += ".validated"
    approvals = list(validated.approval_requirements)
    if any(tool == "device_control.write" for tool in removed):
        approvals.append({"type": "device_action", "allowed_next_step": "deny"})
    return result_type(
        candidate_plan=candidate,
        validated_plan=validated,
        status=status,
        issues=issues,
        removed_tools=removed,
        approval_requirements=approvals,
        execution_mode="draft_only" if any(node.node_type == "workorder" for node in validated.nodes) else "normal",
        post_execution_confirmation_required=any(node.node_type == "workorder" for node in validated.nodes),
    )
