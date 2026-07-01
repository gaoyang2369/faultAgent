"""Deterministic Phase 4.1 shadow planner."""

from __future__ import annotations

from typing import Any

from ..compat import (
    legacy_planning_input_fields_for_compat,
    legacy_projection_for_shadow_plan,
    legacy_projection_warnings_for_planning_input,
    project_route_fields_for_compat,
)
from .contracts import EvidencePlan, NodePlan, OutputPlan, PlanningDecision, PlanningInput, ToolPlan
from .summaries import summarize_shadow_plan

_SQL_TOOLS = ("sql_db_query_checker", "sql_db_query")
_KNOWLEDGE_TOOLS = ("query_knowledge_base",)
_REPORT_TOOLS = ("save_report",)
_NODE_TOOL_MAP = {
    "sql": _SQL_TOOLS,
    "knowledge": _KNOWLEDGE_TOOLS,
    "report": _REPORT_TOOLS,
}
_TOOL_WHITELIST = {"sql_db_query_checker", "sql_db_query", "query_knowledge_base", "save_report"}


def build_planning_input(
    *,
    message: str,
    request_payload_summary: dict[str, Any] | None = None,
    auth_summary: dict[str, Any] | None = None,
    resolved_context: dict[str, Any] | None = None,
    goal_set: dict[str, Any] | None = None,
    legacy_route: dict[str, Any] | None = None,
    task_family: str = "",
    referenced_artifact_id: str | None = None,
    referenced_report_id: str | None = None,
    evidence_refs: list[str] | None = None,
    **legacy_kwargs: Any,
) -> PlanningInput:
    """Build a normalized PlanningInput without side effects."""

    context = dict(resolved_context or {})
    compat_fields = legacy_planning_input_fields_for_compat(legacy_route, legacy_kwargs)
    return PlanningInput(
        message=message,
        request_payload_summary=dict(request_payload_summary or {}),
        auth_summary=dict(auth_summary or {}),
        resolved_context=context,
        goal_set=dict(goal_set or {}),
        **compat_fields,
        task_family=str(task_family or ""),
        referenced_artifact_id=referenced_artifact_id or context.get("referenced_artifact_id"),
        referenced_report_id=referenced_report_id or context.get("referenced_report_id"),
        evidence_refs=list(evidence_refs or _evidence_refs_from_context(context)),
    )


def build_shadow_plan(planning_input: PlanningInput, legacy_plan: dict[str, Any] | None = None) -> PlanningDecision:
    """Return a deterministic shadow planning decision that never mutates legacy execution."""

    legacy = dict(legacy_plan or {})
    warnings: list[str] = []
    goals = _goals(planning_input.goal_set)
    context = planning_input.resolved_context
    relation = str(context.get("relation_to_previous") or "")
    legacy_nodes = _bool_dict(legacy.get("legacy_enabled_nodes") or legacy.get("enabled_nodes"))
    legacy_runtime_tools = _strings(legacy.get("legacy_runtime_tools") or legacy.get("runtime_tools"))
    if "legacy_enabled_nodes" not in legacy and "enabled_nodes" not in legacy:
        warnings.append("missing_legacy_enabled_nodes")
    if "legacy_runtime_tools" not in legacy and "runtime_tools" not in legacy:
        warnings.append("missing_legacy_runtime_tools")

    node_specs: dict[str, dict[str, Any]] = {}
    evidence = EvidencePlan()
    output = OutputPlan(expected_output=_expected_output(planning_input, goals))

    _apply_context_rules(planning_input, evidence, output, warnings)
    _apply_goal_rules(goals, context, node_specs, evidence, output)
    _apply_task_family_rules(planning_input.task_family, node_specs, output)
    _apply_ambiguity_rules(goals, context, node_specs, output)
    _apply_security_context_rules(context, node_specs, evidence, output, warnings)
    _apply_legacy_projection(planning_input, legacy, warnings)

    nodes = _node_plans(node_specs)
    candidate_tools = _candidate_tools(nodes)
    authorized_tools = [tool for tool in candidate_tools if tool in legacy_runtime_tools and tool in _TOOL_WHITELIST]
    denied_tools = _denied_tools(candidate_tools, authorized_tools, legacy_runtime_tools)
    tool_plan = ToolPlan(
        candidate_tools=candidate_tools,
        authorized_runtime_tools=authorized_tools,
        denied_tools=denied_tools,
        whitelist_source="legacy_runtime_tools",
        permission_summary=dict(planning_input.auth_summary or {}),
    )
    if evidence.refresh_required and "evidence_stale" not in output.required_disclosures:
        output.required_disclosures.append("evidence_stale")
    if _has_workorder_goal(goals) or planning_input.task_family == "action_or_workorder":
        output.workorder_boundary = "only_draft_or_confirmation"
        for guardrail in ("no_action_completion_claim", "requires_human_confirmation"):
            if guardrail not in output.final_answer_guardrails:
                output.final_answer_guardrails.append(guardrail)
    if output.expected_output == "report":
        output.report_boundary = "use_authorized_current_or_referenced_evidence_only"

    decision = PlanningDecision(
        nodes=nodes,
        evidence_plan=evidence,
        tool_plan=tool_plan,
        output_plan=output,
        legacy_projection=legacy_projection_for_shadow_plan(
            planning_input,
            {
                **legacy,
                "legacy_enabled_nodes": legacy_nodes,
                "legacy_runtime_tools": legacy_runtime_tools,
            },
        ),
        planner_warnings=list(dict.fromkeys(warnings)),
    )
    decision.planner_summary = _summary(decision)
    return decision


def build_shadow_plan_for_decision(
    *,
    message: str,
    payload: dict[str, Any] | None = None,
    auth_summary: dict[str, Any] | None = None,
    decision: Any,
) -> PlanningDecision:
    """Build a shadow plan from an existing legacy decision after policy/authorization."""

    planning_input = build_planning_input(
        message=message,
        request_payload_summary=_payload_summary(payload or {}),
        auth_summary=auth_summary or {},
        resolved_context=getattr(decision, "resolved_context", {}) or {},
        goal_set=getattr(decision, "goal_set", {}) or {},
        legacy_route=project_route_fields_for_compat(decision),
        task_family=getattr(decision, "task_family", ""),
        referenced_artifact_id=getattr(decision, "referenced_artifact_id", None),
        referenced_report_id=(getattr(decision, "resolved_context", {}) or {}).get("referenced_report_id"),
        evidence_refs=list(getattr(decision, "required_evidence", []) or []),
    )
    return build_shadow_plan(
        planning_input,
        legacy_plan={
            "legacy_enabled_nodes": dict(getattr(decision, "enabled_nodes", {}) or {}),
            "legacy_runtime_tools": list(getattr(decision, "runtime_tools", []) or []),
            "legacy_requested_output": getattr(decision, "requested_output", None),
            "legacy_evidence_mode": getattr(decision, "evidence_mode", None),
            "legacy_should_refresh_runtime_data": getattr(decision, "should_refresh_runtime_data", False),
        },
    )


def attach_shadow_plan_summary(decision: Any, shadow_plan: PlanningDecision) -> None:
    """Attach only compact shadow-plan metadata to a decision-like object."""

    if hasattr(decision, "shadow_plan_summary"):
        decision.shadow_plan_summary = summarize_shadow_plan(shadow_plan)


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = ("equipment_hint", "fault_code_hint", "time_range_hint", "analysis_goal", "needs_sql", "needs_knowledge", "needs_report")
    return {key: payload.get(key) for key in allowed if key in payload and payload.get(key) not in (None, "", [], {})}


def _goals(goal_set: dict[str, Any]) -> list[dict[str, Any]]:
    goals = goal_set.get("goals") if isinstance(goal_set, dict) else []
    return [dict(item) for item in goals or [] if isinstance(item, dict)]


def _goal_type(goal: dict[str, Any]) -> str:
    return str(goal.get("goal_type") or "")


def _goal_id(goal: dict[str, Any]) -> str:
    return str(goal.get("goal_id") or _goal_type(goal))


def _apply_goal_rules(
    goals: list[dict[str, Any]],
    context: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    evidence: EvidencePlan,
    output: OutputPlan,
) -> None:
    for goal in goals:
        goal_type = _goal_type(goal)
        goal_id = _goal_id(goal)
        status = str(goal.get("status") or "ready")
        blocked = status == "blocked"
        if goal_type == "explain_fault_code":
            _enable(nodes, "knowledge", goal_id, "explain fault code", blocked=blocked, required_evidence=["knowledge_source"])
            evidence.required_evidence.append("knowledge_source")
        elif goal_type in {"check_runtime_status", "refresh_current_status"}:
            _enable(nodes, "sql", goal_id, "check or refresh runtime status", blocked=blocked, required_evidence=["runtime_data"])
            evidence.required_evidence.append("runtime_data")
            if goal_type == "refresh_current_status":
                evidence.refresh_required = True
                evidence.disclosure_required.append("fresh_runtime_status_required")
        elif goal_type == "diagnose_fault":
            for node in ("sql", "knowledge", "analysis"):
                _enable(nodes, node, goal_id, "diagnose fault", blocked=blocked, required_evidence=["runtime_data", "knowledge_source"])
            evidence.required_evidence.extend(["runtime_data", "knowledge_source", "diagnosis_basis"])
        elif goal_type == "assess_severity":
            _enable(nodes, "analysis", goal_id, "assess severity", blocked=blocked, required_evidence=["severity_basis"])
            if not context.get("inherited_slots", {}).get("evidence_bundle"):
                _enable(nodes, "sql", goal_id, "severity needs runtime data when no reusable evidence", blocked=blocked, required_evidence=["runtime_data"])
            evidence.required_evidence.append("severity_basis")
        elif goal_type == "recommend_resolution":
            _enable(nodes, "knowledge", goal_id, "recommend resolution", blocked=blocked, required_evidence=["recommended_actions"])
            _enable(nodes, "resolution_recommendation", goal_id, "recommend resolution", blocked=blocked, required_evidence=["recommended_actions"])
            evidence.required_evidence.append("recommended_actions")
        elif goal_type == "generate_report":
            _enable(nodes, "report", goal_id, "generate report", blocked=blocked, required_evidence=["report_evidence"])
            output.expected_output = "report"
            if not _has_reusable_evidence(context):
                _enable(nodes, "analysis", goal_id, "report needs organized evidence", blocked=blocked, required_evidence=["analysis_summary"])
            evidence.required_evidence.append("report_evidence")
        elif goal_type == "decide_workorder":
            _enable(nodes, "permission_check", goal_id, "workorder decision guard", blocked=blocked, guardrails=["permission_required"])
            _enable(nodes, "risk_check", goal_id, "workorder decision guard", blocked=blocked, guardrails=["risk_check_required"])
            _enable(nodes, "audit_log", goal_id, "workorder decision audit guard", blocked=blocked, guardrails=["audit_required"])
            _enable(nodes, "evidence_validation", goal_id, "workorder decision evidence guard", blocked=blocked, guardrails=["evidence_validation_required"])
            _enable(nodes, "output_guardrail", goal_id, "workorder decision output guard", blocked=blocked, guardrails=["output_guardrail_required"])
            _enable(nodes, "workorder_decision", goal_id, "decide workorder draft", blocked=blocked, required_evidence=["workorder_basis"], guardrails=["only_draft_or_confirmation"])
            output.expected_output = "workorder_decision"
            output.workorder_boundary = "only_draft_or_confirmation"
            evidence.required_evidence.append("workorder_basis")
        elif goal_type == "clarify_missing_context":
            _enable(nodes, "final_answer", goal_id, "clarify missing context", state="shadow_only")
            output.expected_output = "clarification"
            output.required_disclosures.append("missing_context")


def _apply_context_rules(
    planning_input: PlanningInput,
    evidence: EvidencePlan,
    output: OutputPlan,
    warnings: list[str],
) -> None:
    context = planning_input.resolved_context
    relation = str(context.get("relation_to_previous") or "")
    inherited = context.get("inherited_slots") if isinstance(context.get("inherited_slots"), dict) else {}
    if relation in {"report_handoff", "action_followup"}:
        reusable = [
            item
            for item in [
                planning_input.referenced_artifact_id,
                inherited.get("evidence_bundle"),
                inherited.get("report"),
            ]
            if item
        ]
        evidence.reusable_evidence.extend(_strings(reusable))
    if relation == "report_handoff":
        output.report_boundary = "reuse_previous_artifact_if_authorized"
    if relation == "action_followup":
        output.workorder_boundary = "only_draft_or_confirmation"
    if context.get("stale_evidence"):
        evidence.refresh_required = True
        evidence.stale_evidence.append("referenced_artifact_or_runtime_status")
        evidence.disclosure_required.append("evidence_stale")
        output.required_disclosures.append("evidence_stale")
    missing = _strings(context.get("missing_context"))
    if missing:
        evidence.missing_evidence.extend(missing)
    if relation in {"correction", "new_case"} and planning_input.referenced_artifact_id:
        warnings.append("referenced_artifact_ignored_for_new_case_or_correction")


def _apply_task_family_rules(task_family: str, nodes: dict[str, dict[str, Any]], output: OutputPlan) -> None:
    if task_family == "knowledge_lookup":
        _enable(nodes, "knowledge", "task_family", "task family suggests knowledge")
    elif task_family == "runtime_status":
        _enable(nodes, "sql", "task_family", "task family suggests runtime status")
    elif task_family == "diagnosis":
        for node in ("sql", "knowledge", "analysis"):
            _enable(nodes, node, "task_family", "task family suggests diagnosis")
    elif task_family == "reporting":
        _enable(nodes, "report", "task_family", "task family suggests report")
        output.expected_output = "report"
    elif task_family == "action_or_workorder":
        for node in ("permission_check", "risk_check", "audit_log", "evidence_validation", "output_guardrail", "workorder_decision"):
            _enable(nodes, node, "task_family", "task family suggests guarded action/workorder", guardrails=["human_confirmation"])
        output.expected_output = "workorder_decision"
        output.workorder_boundary = "only_draft_or_confirmation"
    elif task_family == "meta":
        _enable(nodes, "final_answer", "task_family", "meta/direct response", state="shadow_only")


def _apply_ambiguity_rules(
    goals: list[dict[str, Any]],
    context: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    output: OutputPlan,
) -> None:
    if context.get("relation_to_previous") != "ambiguous":
        return
    for goal in goals:
        goal_type = _goal_type(goal)
        if goal_type != "clarify_missing_context":
            _enable(nodes, "business_goal", _goal_id(goal), "ambiguous reference blocks business goal", state="blocked")
    for node_name, spec in nodes.items():
        if node_name != "final_answer" and spec.get("desired_state") == "enabled":
            spec["desired_state"] = "blocked"
            spec["reason"] = "ambiguous reference blocks tool-bearing business node"
    output.expected_output = "clarification"
    if "missing_context" not in output.required_disclosures:
        output.required_disclosures.append("missing_context")


def _apply_security_context_rules(
    context: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    evidence: EvidencePlan,
    output: OutputPlan,
    warnings: list[str],
) -> None:
    missing = " ".join(_strings(context.get("missing_context")) + [str(context.get("context_resolution_reason") or "")])
    if "权限" not in missing and "authorization" not in missing.lower():
        return
    warnings.append("authorization_limited_context")
    for node_name in ("report", "workorder_decision"):
        if node_name in nodes:
            nodes[node_name]["desired_state"] = "blocked"
            nodes[node_name]["reason"] = "authorization-limited context blocks direct reuse"
    evidence.missing_evidence.append("authorized_context_or_evidence")
    output.expected_output = "clarification"
    output.required_disclosures.append("authorization_limited")


def _apply_legacy_projection(planning_input: PlanningInput, legacy: dict[str, Any], warnings: list[str]) -> None:
    warnings.extend(legacy_projection_warnings_for_planning_input(planning_input))


def _node_plans(specs: dict[str, dict[str, Any]]) -> list[NodePlan]:
    return [
        NodePlan(
            node=node,
            desired_state=str(spec.get("desired_state") or "skipped"),
            reason=str(spec.get("reason") or ""),
            source_goals=_dedupe(spec.get("source_goals") or []),
            required_slots=_dedupe(spec.get("required_slots") or []),
            required_evidence=_dedupe(spec.get("required_evidence") or []),
            guardrails=_dedupe(spec.get("guardrails") or []),
        )
        for node, spec in sorted(specs.items())
    ]


def _enable(
    specs: dict[str, dict[str, Any]],
    node: str,
    source_goal: str,
    reason: str,
    *,
    state: str = "enabled",
    blocked: bool = False,
    required_slots: list[str] | None = None,
    required_evidence: list[str] | None = None,
    guardrails: list[str] | None = None,
) -> None:
    spec = specs.setdefault(
        node,
        {
            "desired_state": "blocked" if blocked else state,
            "reason": reason,
            "source_goals": [],
            "required_slots": [],
            "required_evidence": [],
            "guardrails": [],
        },
    )
    current = str(spec.get("desired_state") or "skipped")
    desired = "blocked" if blocked else state
    if current != "blocked":
        spec["desired_state"] = desired
    if reason and not spec.get("reason"):
        spec["reason"] = reason
    spec.setdefault("source_goals", []).append(source_goal)
    spec.setdefault("required_slots", []).extend(required_slots or [])
    spec.setdefault("required_evidence", []).extend(required_evidence or [])
    spec.setdefault("guardrails", []).extend(guardrails or [])


def _candidate_tools(nodes: list[NodePlan]) -> list[str]:
    tools: list[str] = []
    for node in nodes:
        if node.desired_state != "enabled":
            continue
        tools.extend(_NODE_TOOL_MAP.get(node.node, ()))
    return _dedupe(tools)


def _denied_tools(candidate_tools: list[str], authorized_tools: list[str], legacy_runtime_tools: list[str]) -> list[dict[str, str]]:
    denied: list[dict[str, str]] = []
    authorized = set(authorized_tools)
    legacy = set(legacy_runtime_tools)
    for tool in candidate_tools:
        if tool in authorized:
            continue
        reason = "not_in_legacy_runtime_tools" if tool not in legacy else "not_authorized"
        denied.append({"tool": tool, "reason": reason})
    for tool in sorted(_TOOL_WHITELIST - set(candidate_tools)):
        denied.append({"tool": tool, "reason": "not_required_by_goal"})
    return denied


def _expected_output(planning_input: PlanningInput, goals: list[dict[str, Any]]) -> str:
    goal_types = {_goal_type(goal) for goal in goals}
    if "clarify_missing_context" in goal_types:
        return "clarification"
    if "generate_report" in goal_types:
        return "report"
    if "decide_workorder" in goal_types or planning_input.task_family == "action_or_workorder":
        return "workorder_decision"
    if planning_input.task_family == "meta":
        return "answer"
    return "answer"


def _has_workorder_goal(goals: list[dict[str, Any]]) -> bool:
    return any(_goal_type(goal) == "decide_workorder" for goal in goals)


def _has_reusable_evidence(context: dict[str, Any]) -> bool:
    inherited = context.get("inherited_slots") if isinstance(context.get("inherited_slots"), dict) else {}
    return bool(context.get("referenced_artifact_id") or inherited.get("evidence_bundle") or inherited.get("report"))


def _evidence_refs_from_context(context: dict[str, Any]) -> list[str]:
    inherited = context.get("inherited_slots") if isinstance(context.get("inherited_slots"), dict) else {}
    return _strings([context.get("referenced_artifact_id"), context.get("referenced_report_id"), inherited.get("evidence_bundle")])


def _bool_dict(value: Any) -> dict[str, bool]:
    return {str(key): bool(item) for key, item in dict(value or {}).items()}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _summary(decision: PlanningDecision) -> str:
    compact = summarize_shadow_plan(decision)
    enabled = ", ".join(compact.get("enabled_node_names") or []) or "none"
    tools = ", ".join(compact.get("authorized_runtime_tools") or []) or "none"
    return f"shadow planner suggests nodes=[{enabled}], authorized_tools=[{tools}], output={compact.get('expected_output')}"
