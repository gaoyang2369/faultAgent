from __future__ import annotations

from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.planning import apply_planner_gate_to_decision, build_planner_gate


def _decision(
    *,
    primary_task_type: str = "alarm_triage",
    enabled_nodes: dict[str, bool] | None = None,
    runtime_tools: list[str] | None = None,
    goals: list[dict] | None = None,
    objects: dict | None = None,
    resolved_context: dict | None = None,
    intent_stack: list[str] | None = None,
) -> SingleAgentDecision:
    goals = goals or [{"goal_type": "check_runtime_status", "risk_level": "read_only"}]
    return SingleAgentDecision(
        primary_task_type=primary_task_type,
        task_family="diagnosis",
        enabled_nodes=enabled_nodes
        or {
            "sql": True,
            "knowledge": True,
            "analysis": True,
            "resolution_recommendation": True,
            "workorder_decision": True,
            "evidence_validation": True,
            "output_guardrail": True,
        },
        runtime_tools=runtime_tools or ["sql_db_query_checker", "sql_db_query", "query_knowledge_base"],
        objects=objects or {"device_ids": ["J1"], "alarm_codes": ["A07089"]},
        intent_stack=intent_stack or ["explain_alarm_code", "check_current_status", "resolution_recommendation"],
        goals=goals,
        goal_set={"goals": goals},
        resolved_context=resolved_context or {"relation_to_previous": "new_case", "stale_evidence": False},
        authorization={"mode": "allow"},
    )


def _shadow(
    *,
    nodes: list[str] | None = None,
    tools: list[str] | None = None,
    missing_evidence: list[str] | None = None,
    disclosures: list[str] | None = None,
    output_plan: dict | None = None,
) -> dict:
    nodes = nodes or ["sql", "knowledge", "analysis", "resolution_recommendation", "evidence_validation", "output_guardrail"]
    tools = tools or ["sql_db_query", "query_knowledge_base"]
    return {
        "nodes": [{"node": node, "desired_state": "enabled"} for node in nodes],
        "tool_plan": {"candidate_tools": tools, "authorized_runtime_tools": tools},
        "evidence_plan": {
            "required_evidence": ["runtime_data", "knowledge_source", "diagnosis_basis"],
            "missing_evidence": missing_evidence or [],
            "refresh_required": False,
            "disclosure_required": disclosures or [],
        },
        "output_plan": output_plan or {"expected_output": "answer", "required_disclosures": disclosures or []},
    }


def _gate(
    decision: SingleAgentDecision,
    shadow: dict | None = None,
    *,
    enabled: bool = True,
    dry_run: bool = False,
    diagnosis_active: bool = True,
    diff: dict | None = None,
) -> object:
    return build_planner_gate(
        decision=decision,
        shadow_plan=shadow or _shadow(),
        planning_diff=diff or {"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={
            "enabled": enabled,
            "dry_run": dry_run,
            "diagnosis_active": diagnosis_active,
            "diagnosis_dry_run": True,
        },
    )


def _readiness(gate: object) -> dict:
    return gate.safety_summary["diagnosis_readiness"]


def test_default_disabled_diagnosis_does_not_active() -> None:
    decision = _decision()
    gate = _gate(decision, enabled=False, diagnosis_active=False)

    assert gate.selected_execution_source == "legacy_policy"
    assert _readiness(gate)["active_allowed"] is False


def test_dry_run_candidate_stays_legacy_policy() -> None:
    gate = _gate(_decision(), dry_run=True, diagnosis_active=True)

    assert gate.mode == "dry_run"
    assert gate.selected_execution_source == "legacy_policy"
    assert _readiness(gate)["recommended_next_phase"] == "candidate_for_limited_active"


def test_active_alarm_triage_eligible_uses_limited_projection() -> None:
    decision = _decision(primary_task_type="alarm_triage")
    before_tools = set(decision.runtime_tools)

    gate = _gate(decision)
    apply_planner_gate_to_decision(decision, gate)

    assert gate.selected_execution_source == "planner_gated"
    assert set(gate.active_scope) == {"sql", "knowledge", "analysis", "resolution_recommendation"}
    assert "workorder_decision" not in gate.final_enabled_nodes
    assert {"evidence_validation", "output_guardrail"}.issubset(set(gate.final_enabled_nodes))
    assert set(gate.final_runtime_tools).issubset(before_tools)
    assert _readiness(gate)["ready_for_active"] is True
    assert _readiness(gate)["active_mode"] == "limited_explanation"


def test_active_fault_diagnosis_eligible_does_not_enable_workorder() -> None:
    decision = _decision(primary_task_type="fault_diagnosis", goals=[{"goal_type": "diagnose_fault", "risk_level": "read_only"}])
    gate = _gate(decision)

    assert gate.selected_execution_source == "planner_gated"
    assert set(gate.active_scope) == {"sql", "knowledge", "analysis", "resolution_recommendation"}
    assert "workorder_decision" not in gate.final_enabled_nodes


def test_rca_and_health_are_blocked_by_default() -> None:
    rca = _gate(_decision(primary_task_type="root_cause_analysis"))
    health = _gate(_decision(primary_task_type="health_assessment", objects={"device_ids": ["J1"], "alarm_codes": []}))

    assert rca.selected_execution_source == "legacy_policy"
    assert "root_cause_analysis_not_migrated" in rca.blockers
    assert health.selected_execution_source == "legacy_policy"
    assert "health_assessment_not_migrated" in health.blockers


def test_stale_missing_disclosure_is_blocked() -> None:
    decision = _decision(resolved_context={"relation_to_previous": "continuation", "stale_evidence": True})
    gate = _gate(decision, _shadow(disclosures=[]))

    assert gate.selected_execution_source == "legacy_policy"
    assert "stale_evidence_without_disclosure" in gate.blockers


def test_missing_runtime_status_is_blocked() -> None:
    decision = _decision(enabled_nodes={"knowledge": True, "analysis": True}, runtime_tools=["query_knowledge_base"])
    gate = _gate(decision, _shadow(nodes=["knowledge", "analysis"], tools=["query_knowledge_base"]))

    assert gate.selected_execution_source == "legacy_policy"
    assert "missing_runtime_status" in gate.blockers


def test_missing_manual_reference_is_blocked() -> None:
    decision = _decision(enabled_nodes={"sql": True, "analysis": True}, runtime_tools=["sql_db_query"])
    gate = _gate(decision, _shadow(nodes=["sql", "analysis"], tools=["sql_db_query"]))

    assert gate.selected_execution_source == "legacy_policy"
    assert "missing_manual_reference" in gate.blockers


def test_no_supporting_evidence_is_blocked() -> None:
    gate = _gate(_decision(), _shadow(missing_evidence=["claim_supporting_evidence"]))

    assert gate.selected_execution_source == "legacy_policy"
    assert "claims_without_supporting_evidence" in gate.blockers


def test_action_workorder_goal_is_blocked() -> None:
    decision = _decision(goals=[{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}])
    gate = _gate(decision)

    assert gate.selected_execution_source == "legacy_policy"
    assert "action_or_workorder_not_migrated" in gate.blockers


def test_ambiguous_context_is_blocked() -> None:
    decision = _decision(resolved_context={"relation_to_previous": "ambiguous", "stale_evidence": False})
    gate = _gate(decision)

    assert gate.selected_execution_source == "legacy_policy"
    assert "blocked_context_relation:ambiguous" in gate.blockers


def test_unauthorized_inheritance_is_blocked() -> None:
    decision = _decision(resolved_context={"relation_to_previous": "continuation", "context_resolution_reason": "授权范围不足"})
    decision.authorization = {"mode": "deny", "denied_reason_code": "asset_out_of_scope"}
    gate = _gate(decision)

    assert gate.selected_execution_source == "legacy_policy"
    assert "unauthorized_inherited_artifact" in gate.blockers


def test_needs_review_and_critical_diff_are_blocked() -> None:
    needs_review = _gate(_decision(), diff={"overall_status": "needs_review", "severity": "warning", "counters": {}})
    critical = _gate(_decision(), diff={"overall_status": "unsafe_mismatch", "severity": "critical", "counters": {"critical_count": 1}})

    assert needs_review.selected_execution_source == "legacy_policy"
    assert "diff_status_not_allowed" in needs_review.blockers
    assert critical.selected_execution_source == "legacy_policy"
    assert "critical_diff_present" in critical.blockers


def test_shadow_authorized_extra_tool_is_blocked() -> None:
    gate = _gate(_decision(), _shadow(tools=["sql_db_query", "query_knowledge_base", "save_report"]))

    assert gate.selected_execution_source == "legacy_policy"
    assert "tool_scope_violation" in gate.blockers


def test_evidence_validation_or_output_guardrail_removal_is_blocked() -> None:
    gate = _gate(_decision(), _shadow(nodes=["sql", "knowledge", "analysis"], tools=["sql_db_query", "query_knowledge_base"]))

    assert gate.selected_execution_source == "legacy_policy"
    assert "safety_node_removed" in gate.blockers


def test_executed_semantics_in_shadow_output_are_blocked() -> None:
    gate = _gate(
        _decision(),
        _shadow(output_plan={"expected_output": "answer", "required_disclosures": [], "final_answer_guardrails": ["已执行复位"]}),
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert "unsafe_action_completion_semantics" in gate.blockers
