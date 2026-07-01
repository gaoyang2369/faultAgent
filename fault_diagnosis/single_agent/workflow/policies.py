"""Workflow policy registry and node-resolution helpers."""

from __future__ import annotations

from ..compat import (
    build_task_payload_for_compat,
    goal_types,
    legacy_task_value,
    route_is_action_request,
    route_requests_workorder_followup,
)
from .contracts import NodeSetting, TaskRoute, TaskType, WorkflowPlan, WorkflowPolicy

_SQL_TOOLS = ["sql_db_query_checker", "sql_db_query"]
_KNOWLEDGE_TOOLS = ["query_knowledge_base"]
_REPORT_TOOLS = ["save_report"]


def _policy(
    *,
    policy_id: str,
    task_type: TaskType,
    workflow_id: str,
    required_slots: list[str],
    conditional_required_slots: dict[str, list[str]] | None,
    enabled_nodes: dict[str, NodeSetting],
    evidence_requirements: dict[str, bool],
    output_schema: str,
    on_missing_evidence: str,
    guardrails: list[str],
) -> WorkflowPolicy:
    return WorkflowPolicy(
        policy_id=policy_id,
        task_type=task_type,
        workflow_id=workflow_id,
        required_slots=required_slots,
        conditional_required_slots=conditional_required_slots or {},
        allowed_tools=[
            "knowledge_base.search",
            "asset_db.read",
            "timeseries_db.read",
            "alarm_db.read",
            "event_log.read",
            "workorder_db.read",
            "report_store.write_draft",
        ],
        forbidden_tools=[
            "device_control.write",
            "config.write",
            "workorder.dispatch",
            "alarm.acknowledge",
            "alarm.close",
        ],
        enabled_nodes=enabled_nodes,
        evidence_requirements=evidence_requirements,
        output_schema=output_schema,
        on_missing_evidence=on_missing_evidence,
        guardrails=guardrails,
    )


POLICIES: dict[TaskType, WorkflowPolicy] = {
    TaskType.STATUS_QUERY: _policy(
        policy_id="status_query_v1",
        task_type=TaskType.STATUS_QUERY,
        workflow_id="wf_status_query_v1",
        required_slots=["asset_context"],
        conditional_required_slots={"workorder_decision": ["current_abnormal_status"]},
        enabled_nodes={
            "sql": True,
            "knowledge": False,
            "analysis": True,
            "resolution_recommendation": False,
            "workorder_decision": "conditional",
            "report": False,
        },
        evidence_requirements={
            "need_asset_identity": True,
            "need_current_status": True,
            "need_metric_timestamp": True,
        },
        output_schema="status_query_answer_v1",
        on_missing_evidence="answer_available_status_and_mark_unknowns",
        guardrails=[
            "no_current_status_claim_without_runtime_data",
            "show_data_freshness",
            "no_workorder_dispatch_without_human_confirmation",
        ],
    ),
    TaskType.ALARM_TRIAGE: _policy(
        policy_id="alarm_triage_v1",
        task_type=TaskType.ALARM_TRIAGE,
        workflow_id="wf_alarm_triage_v1",
        required_slots=["alarm_code_or_name"],
        conditional_required_slots={
            "check_current_fault_status": ["device_id"],
            "workorder_decision": ["device_id", "current_alarm_status"],
        },
        enabled_nodes={
            "sql": "conditional",
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": "conditional",
            "report": False,
        },
        evidence_requirements={
            "need_alarm_definition": True,
            "need_alarm_severity": True,
            "need_recommended_actions": True,
            "need_current_alarm_status_if_device_provided": True,
        },
        output_schema="alarm_triage_answer_v1",
        on_missing_evidence="answer_available_subgoals_and_mark_blocked_subgoals",
        guardrails=[
            "no_current_fault_claim_without_realtime_data",
            "no_workorder_dispatch_without_human_confirmation",
            "show_uncertainty",
            "cite_evidence_ids",
        ],
    ),
    TaskType.FAULT_DIAGNOSIS: _policy(
        policy_id="fault_diagnosis_v1",
        task_type=TaskType.FAULT_DIAGNOSIS,
        workflow_id="wf_fault_diagnosis_v1",
        required_slots=["asset_context", "symptom_or_alarm"],
        conditional_required_slots={},
        enabled_nodes={
            "collect_asset_context": "conditional",
            "sql": True,
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": True,
            "report": "conditional",
        },
        evidence_requirements={
            "need_runtime_data": True,
            "need_supporting_evidence_for_each_cause": True,
            "need_missing_evidence_disclosure": True,
        },
        output_schema="fault_diagnosis_answer_v1",
        on_missing_evidence="lower_confidence_and_disclose_missing_evidence",
        guardrails=[
            "do_not_confirm_root_cause_without_causal_evidence",
            "separate_symptom_cause_and_root_cause",
            "no_control_action_without_approval",
        ],
    ),
    TaskType.ROOT_CAUSE_ANALYSIS: _policy(
        policy_id="root_cause_analysis_v1",
        task_type=TaskType.ROOT_CAUSE_ANALYSIS,
        workflow_id="wf_root_cause_analysis_v1",
        required_slots=["event_or_asset_context", "time_window"],
        conditional_required_slots={"workorder_decision": ["open_risk"]},
        enabled_nodes={
            "sql": True,
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": "conditional",
            "report": True,
        },
        evidence_requirements={
            "need_event_timeline": True,
            "need_causal_support": True,
            "need_impact_scope": True,
            "need_unknowns": True,
        },
        output_schema="rca_answer_v1",
        on_missing_evidence="avoid_root_cause_claim_and_mark_hypothesis",
        guardrails=[
            "do_not_turn_correlation_into_causality",
            "root_cause_requires_temporal_and_mechanism_support",
            "show_unknowns",
        ],
    ),
    TaskType.HEALTH_ASSESSMENT: _policy(
        policy_id="health_assessment_v1",
        task_type=TaskType.HEALTH_ASSESSMENT,
        workflow_id="wf_health_assessment_v1",
        required_slots=["asset_or_group_context"],
        conditional_required_slots={"workorder_decision": ["high_risk_or_degradation"]},
        enabled_nodes={
            "sql": True,
            "knowledge": "conditional",
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": "conditional",
            "report": "conditional",
        },
        evidence_requirements={
            "need_trend_window": True,
            "need_enough_data_points": True,
            "need_risk_rule_reference_if_scored": True,
        },
        output_schema="health_assessment_answer_v1",
        on_missing_evidence="answer_observed_health_and_disclose_prediction_limits",
        guardrails=[
            "do_not_present_prediction_as_fact",
            "show_assessment_window",
            "show_data_sufficiency",
        ],
    ),
    TaskType.KNOWLEDGE_QA: _policy(
        policy_id="knowledge_qa_v1",
        task_type=TaskType.KNOWLEDGE_QA,
        workflow_id="wf_knowledge_qa_v1",
        required_slots=["topic_or_alarm_or_operation"],
        conditional_required_slots={"sql": ["device_id_when_device_specific"]},
        enabled_nodes={
            "sql": "conditional",
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": "conditional",
            "workorder_decision": False,
            "report": False,
        },
        evidence_requirements={
            "need_knowledge_source": True,
            "need_applicability_scope": True,
            "need_safety_notes_for_risky_operation": True,
        },
        output_schema="knowledge_qa_answer_v1",
        on_missing_evidence="answer_from_sources_and_mark_applicability_limits",
        guardrails=[
            "no_manual_claim_without_source",
            "show_model_or_version_limits",
            "no_workorder_dispatch_without_human_confirmation",
        ],
    ),
    TaskType.REPORT_GENERATION: _policy(
        policy_id="report_generation_v1",
        task_type=TaskType.REPORT_GENERATION,
        workflow_id="wf_report_generation_v1",
        required_slots=["report_type_or_existing_evidence"],
        conditional_required_slots={},
        enabled_nodes={
            "sql": "conditional",
            "knowledge": "conditional",
            "analysis": True,
            "resolution_recommendation": "conditional",
            "workorder_decision": False,
            "report": True,
        },
        evidence_requirements={
            "need_existing_or_fresh_evidence_bundle": True,
            "need_report_time_window": True,
            "need_claim_evidence_links": True,
        },
        output_schema="report_answer_v1",
        on_missing_evidence="generate_report_with_limitations",
        guardrails=[
            "report_only_uses_evidence_bundle",
            "no_unverified_claims_in_report",
            "show_report_window",
        ],
    ),
    TaskType.ACTION_REQUEST: _policy(
        policy_id="action_request_v1",
        task_type=TaskType.ACTION_REQUEST,
        workflow_id="wf_action_request_v1",
        required_slots=["action_type"],
        conditional_required_slots={"execute_if_allowed": ["permission", "approval", "safe_state"]},
        enabled_nodes={
            "permission_check": True,
            "risk_check": True,
            "sql": True,
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": "conditional",
            "report": False,
            "audit_log": True,
        },
        evidence_requirements={
            "need_permission_result": True,
            "need_risk_result": True,
            "need_precondition_evidence": True,
            "need_human_confirmation_for_write": True,
        },
        output_schema="action_request_answer_v1",
        on_missing_evidence="deny_or_request_confirmation",
        guardrails=[
            "no_device_control_execution",
            "no_config_write_execution",
            "no_workorder_dispatch_without_human_confirmation",
            "audit_write_intent",
        ],
    ),
    TaskType.PERMISSION_SCOPE_QUERY: _policy(
        policy_id="permission_scope_query_v1",
        task_type=TaskType.PERMISSION_SCOPE_QUERY,
        workflow_id="wf_permission_scope_query_v1",
        required_slots=[],
        conditional_required_slots={},
        enabled_nodes={
            "sql": False,
            "knowledge": False,
            "analysis": True,
            "resolution_recommendation": False,
            "workorder_decision": False,
            "report": False,
        },
        evidence_requirements={
            "need_auth_context": True,
        },
        output_schema="permission_scope_answer_v1",
        on_missing_evidence="answer_from_authorization_context",
        guardrails=[
            "answer_permissions_from_server_auth_context",
            "do_not_reuse_device_context_for_identity_question",
        ],
    ),
}


def get_policy(task_type: TaskType | str) -> WorkflowPolicy:
    """Deprecated fallback policy lookup for legacy task classifiers."""

    if isinstance(task_type, TaskType):
        normalized = task_type
    else:
        try:
            normalized = TaskType(str(task_type))
        except ValueError:
            normalized = TaskType.FAULT_DIAGNOSIS
    return POLICIES[normalized]


def select_policy_from_intent_axes(route: TaskRoute) -> WorkflowPolicy:
    """Select policy from task family, GoalSet, context and readiness axes.

    The legacy classifier remains as a compatibility fallback. When the new
    axes disagree with the legacy-selected policy, keep legacy execution.
    """

    legacy_policy = get_policy(legacy_task_value(route))
    axis_task = _task_type_from_axes(route)
    if axis_task is None:
        return legacy_policy
    axis_policy = POLICIES[axis_task]
    if axis_policy.policy_id != legacy_policy.policy_id:
        return legacy_policy
    return axis_policy


def build_workflow_plan(route: TaskRoute, *, needs_report: bool = False) -> WorkflowPlan:
    """Resolve policy nodes and runtime tool allowlist for a route."""

    policy = select_policy_from_intent_axes(route)
    plan_mode_nodes = _nodes_for_plan_mode(route.plan_mode)
    if plan_mode_nodes is not None:
        return WorkflowPlan(
            route=route,
            policy=policy,
            resolved_nodes=plan_mode_nodes,
            runtime_tools=_runtime_tools_for_nodes(plan_mode_nodes),
            metadata={
                "missing_required_slots": _missing_required_slots(route, policy),
                "blocked_subgoals": [
                    item.model_dump(exclude_none=True)
                    for item in route.subgoals
                    if item.status == "blocked"
                ],
                **build_task_payload_for_compat(route),
                "plan_mode": route.plan_mode,
                "evidence_mode": route.evidence_mode,
            },
        )
    node_names = set(policy.enabled_nodes)
    node_names.update(resolve_nodes_from_goals(route))
    resolved_nodes = {
        node_name: _resolve_node(
            node_name,
            policy.enabled_nodes.get(node_name, "conditional"),
            policy=policy,
            route=route,
            needs_report=needs_report,
        )
        for node_name in sorted(node_names)
    }
    return WorkflowPlan(
        route=route,
        policy=policy,
        resolved_nodes=resolved_nodes,
        runtime_tools=_runtime_tools_for_nodes(resolved_nodes),
        metadata={
            "missing_required_slots": _missing_required_slots(route, policy),
            "blocked_subgoals": [
                item.model_dump(exclude_none=True)
                for item in route.subgoals
                if item.status == "blocked"
            ],
            **build_task_payload_for_compat(route),
            "plan_mode": route.plan_mode,
            "evidence_mode": route.evidence_mode,
        },
    )


def _nodes_for_plan_mode(plan_mode: str) -> dict[str, bool] | None:
    if plan_mode == "workorder_decision_from_artifact":
        return {
            "permission_check": True,
            "risk_check": True,
            "sql": False,
            "knowledge": False,
            "analysis": False,
            "resolution_recommendation": False,
            "workorder_decision": True,
            "report": False,
            "evidence_validation": True,
            "output_guardrail": True,
            "audit_log": True,
        }
    if plan_mode == "status_refresh_then_workorder":
        return {
            "permission_check": True,
            "risk_check": True,
            "sql": True,
            "knowledge": False,
            "analysis": False,
            "resolution_recommendation": False,
            "workorder_decision": True,
            "report": False,
            "evidence_validation": True,
            "output_guardrail": True,
            "audit_log": True,
        }
    return None


def _task_type_from_axes(route: TaskRoute) -> TaskType | None:
    goals = set(goal_types(route))
    task_family = str(route.task_family or "")
    relation = str((route.resolved_context or {}).get("relation_to_previous") or route.relation_to_previous or "")
    if "answer_meta_question" in goals or task_family == "meta":
        return TaskType.PERMISSION_SCOPE_QUERY
    if "generate_report" in goals or route.requested_output == "report" or relation == "report_handoff":
        return TaskType.REPORT_GENERATION
    if task_family == "action_or_workorder" or route.action_type:
        return TaskType.ACTION_REQUEST
    if task_family == "runtime_status" or goals.intersection({"check_runtime_status", "refresh_current_status"}):
        if not goals.difference({"check_runtime_status", "refresh_current_status"}):
            return TaskType.STATUS_QUERY
    if task_family == "knowledge_lookup":
        return TaskType.KNOWLEDGE_QA
    if task_family == "diagnosis":
        if "diagnose_fault" in goals:
            return TaskType.FAULT_DIAGNOSIS
        if goals.intersection({"explain_fault_code", "recommend_resolution"}) and goals.intersection(
            {"check_runtime_status", "refresh_current_status", "assess_severity"}
        ):
            return TaskType.ALARM_TRIAGE
        if goals == {"assess_severity"}:
            return TaskType.HEALTH_ASSESSMENT
    return None


def resolve_nodes_from_goals(route: TaskRoute) -> set[str]:
    """Resolve node requirements from GoalSet/task-family/readiness axes."""

    goals = set(goal_types(route))
    nodes: set[str] = {"analysis"}
    if "explain_fault_code" in goals:
        nodes.add("knowledge")
    if goals.intersection({"check_runtime_status", "refresh_current_status"}):
        nodes.add("sql")
    if goals.intersection({"diagnose_fault", "assess_severity"}):
        nodes.update({"sql", "knowledge", "analysis"})
    if "recommend_resolution" in goals:
        nodes.update({"knowledge", "analysis", "resolution_recommendation"})
    if "generate_report" in goals or route.requested_output == "report":
        nodes.add("report")
    if route_is_action_request(route):
        nodes.update(
            {
                "permission_check",
                "risk_check",
                "sql",
                "knowledge",
                "analysis",
                "resolution_recommendation",
                "audit_log",
            }
        )
    if route_requests_workorder_followup(route) and (route.flags.get("need_workorder_decision") or route.action_target == "workorder"):
        nodes.update({"permission_check", "risk_check", "workorder_decision", "audit_log"})
    return nodes


def _resolve_node(
    node_name: str,
    setting: NodeSetting,
    *,
    policy: WorkflowPolicy,
    route: TaskRoute,
    needs_report: bool,
) -> bool:
    if isinstance(setting, bool):
        return setting
    flags = route.flags
    if node_name == "sql":
        goals = set(goal_types(route))
        if goals.intersection({"check_runtime_status", "refresh_current_status"}) and not route.has_device_context():
            return False
        if "assess_severity" in goals and route.has_device_context():
            return True
        if policy.task_type in {TaskType.ALARM_TRIAGE, TaskType.KNOWLEDGE_QA}:
            return bool(route.has_device_context() and flags.get("need_sql"))
        if policy.task_type == TaskType.REPORT_GENERATION:
            return bool(flags.get("need_sql"))
        return bool(
            flags.get("need_sql")
            or route.has_device_context()
            or route_is_action_request(route)
        )
    if node_name == "knowledge":
        goals = set(goal_types(route))
        return bool(
            flags.get("need_knowledge")
            or route.objects.alarm_codes
            or flags.get("need_resolution")
            or "explain_fault_code" in goals
        )
    if node_name == "resolution_recommendation":
        return bool(flags.get("need_resolution") or flags.get("need_analysis"))
    if node_name == "workorder_decision":
        return bool(flags.get("need_workorder_decision") and (route.has_device_context() or route.referenced_artifact_id))
    if node_name in {"permission_check", "risk_check", "audit_log"}:
        return bool(flags.get(f"need_{node_name}") or route.action_target == "workorder")
    if node_name == "report":
        return bool(needs_report or flags.get("need_report") or route.requested_output == "report")
    if node_name == "collect_asset_context":
        return route.has_device_context()
    return bool(flags.get(node_name))


def _runtime_tools_for_nodes(nodes: dict[str, bool]) -> list[str]:
    tools: list[str] = []
    if nodes.get("sql"):
        tools.extend(_SQL_TOOLS)
    if nodes.get("knowledge"):
        tools.extend(_KNOWLEDGE_TOOLS)
    if nodes.get("report"):
        tools.extend(_REPORT_TOOLS)
    return list(dict.fromkeys(tools))


def _missing_required_slots(route: TaskRoute, policy: WorkflowPolicy) -> list[str]:
    missing = set(route.missing_slots)
    for slot in policy.required_slots:
        if slot == "asset_context" and not route.has_device_context():
            missing.add(slot)
        elif slot == "alarm_code_or_name" and not route.objects.alarm_codes and not route.objects.topics:
            missing.add(slot)
        elif slot == "topic_or_alarm_or_operation" and not (
            route.objects.alarm_codes or route.objects.topics or route.user_goal
        ):
            missing.add(slot)
        elif slot == "time_window" and route.time_window.is_inferred:
            missing.add(slot)
        elif slot == "action_type" and not route.action_type:
            missing.add(slot)
    return sorted(missing)
