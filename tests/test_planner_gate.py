from __future__ import annotations

from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.planning import (
    apply_planner_gate_to_decision,
    build_planner_gate,
    build_planning_diff,
)


def _shadow(*, node: str = "knowledge", tool: str = "query_knowledge_base", output: str = "answer") -> dict:
    return {
        "nodes": [{"node": node, "desired_state": "enabled"}],
        "tool_plan": {"candidate_tools": [tool], "authorized_runtime_tools": [tool]},
        "evidence_plan": {"refresh_required": False, "disclosure_required": []},
        "output_plan": {"expected_output": output, "required_disclosures": []},
    }


def _decision(
    *,
    task_family: str = "knowledge_lookup",
    primary_task_type: str = "knowledge_qa",
    node: str = "knowledge",
    tool: str = "query_knowledge_base",
    goals: list[dict] | None = None,
    relation: str = "new_case",
) -> SingleAgentDecision:
    return SingleAgentDecision(
        primary_task_type=primary_task_type,
        task_family=task_family,
        enabled_nodes={node: True},
        runtime_tools=[tool],
        goal_set={"goals": goals or [{"goal_id": "g1", "goal_type": "explain_fault_code", "risk_level": "read_only"}]},
        goals=goals or [{"goal_id": "g1", "goal_type": "explain_fault_code", "risk_level": "read_only"}],
        resolved_context={"relation_to_previous": relation},
        authorization={"mode": "allow"},
    )


def _diff(decision: SingleAgentDecision, shadow: dict) -> object:
    return build_planning_diff({}, shadow, decision=decision)


def test_default_disabled_keeps_legacy_policy() -> None:
    decision = _decision()
    shadow = _shadow()
    before_nodes = dict(decision.enabled_nodes)
    before_tools = list(decision.runtime_tools)

    gate = build_planner_gate(decision=decision, shadow_plan=shadow, planning_diff=_diff(decision, shadow))
    apply_planner_gate_to_decision(decision, gate)

    assert gate.mode == "disabled"
    assert gate.selected_execution_source == "legacy_policy"
    assert decision.enabled_nodes == before_nodes
    assert decision.runtime_tools == before_tools


def test_dry_run_eligible_does_not_change_execution() -> None:
    decision = _decision()
    shadow = _shadow()

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff=_diff(decision, shadow),
        config_overrides={"enabled": True, "dry_run": True},
    )
    apply_planner_gate_to_decision(decision, gate)

    assert gate.mode == "dry_run"
    assert gate.eligible is True
    assert gate.selected_execution_source == "legacy_policy"
    assert decision.enabled_nodes == {"knowledge": True}
    assert decision.runtime_tools == ["query_knowledge_base"]


def test_active_eligible_knowledge_uses_intersection_only() -> None:
    decision = _decision()
    shadow = _shadow()

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff=_diff(decision, shadow),
        config_overrides={"enabled": True, "dry_run": False},
    )
    apply_planner_gate_to_decision(decision, gate)

    assert gate.selected_execution_source == "planner_gated"
    assert gate.final_runtime_tools == ["query_knowledge_base"]
    assert decision.runtime_tools == ["query_knowledge_base"]
    assert decision.enabled_nodes == {"knowledge": True}


def test_active_projection_preserves_shared_safety_nodes() -> None:
    decision = _decision()
    decision.enabled_nodes = {"knowledge": True, "output_guardrail": True, "evidence_validation": True}
    shadow = {
        "nodes": [
            {"node": "knowledge", "desired_state": "enabled"},
            {"node": "output_guardrail", "desired_state": "enabled"},
            {"node": "evidence_validation", "desired_state": "enabled"},
        ],
        "tool_plan": {"candidate_tools": ["query_knowledge_base"], "authorized_runtime_tools": ["query_knowledge_base"]},
        "evidence_plan": {"refresh_required": False, "disclosure_required": []},
        "output_plan": {"expected_output": "answer", "required_disclosures": []},
    }

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )
    apply_planner_gate_to_decision(decision, gate)

    assert gate.selected_execution_source == "planner_gated"
    assert set(gate.final_enabled_nodes) == {"knowledge", "output_guardrail", "evidence_validation"}
    assert decision.enabled_nodes["output_guardrail"] is True
    assert decision.enabled_nodes["evidence_validation"] is True


def test_active_eligible_runtime_status_only_allows_sql() -> None:
    decision = _decision(task_family="runtime_status", primary_task_type="status_query", node="sql", tool="sql_db_query")
    shadow = _shadow(node="sql", tool="sql_db_query")

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff=_diff(decision, shadow),
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "planner_gated"
    assert gate.final_enabled_nodes == ["sql"]
    assert gate.final_runtime_tools == ["sql_db_query"]
    assert "save_report" not in gate.final_runtime_tools


def test_report_handoff_active_only_allows_report_node() -> None:
    decision = _decision(task_family="reporting", primary_task_type="report_generation", node="report", tool="save_report")
    decision.resolved_context = {"relation_to_previous": "report_handoff", "stale_evidence": False}
    shadow = _shadow(node="report", tool="save_report", output="report")

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff=_diff(decision, shadow),
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "planner_gated"
    assert gate.final_enabled_nodes == ["report"]
    assert gate.final_runtime_tools == ["save_report"]


def test_diagnosis_is_blocked() -> None:
    decision = _decision(task_family="diagnosis", primary_task_type="fault_diagnosis", goals=[{"goal_type": "diagnose_fault"}])
    gate = build_planner_gate(
        decision=decision,
        shadow_plan=_shadow(),
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert gate.mode == "dry_run"
    assert "unsupported_task_family" not in gate.blockers
    assert "diagnosis_dry_run_only" in gate.blockers
    assert "diagnosis_active_not_enabled" in gate.blockers
    assert gate.safety_summary["diagnosis_readiness"]["ready_for_active"] is False


def test_action_workorder_is_blocked() -> None:
    decision = _decision(task_family="action_or_workorder", primary_task_type="action_request", goals=[{"goal_type": "decide_workorder"}])
    gate = build_planner_gate(
        decision=decision,
        shadow_plan=_shadow(node="workorder_decision", tool="save_report", output="workorder_decision"),
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert "action_or_workorder_not_migrated" in gate.blockers


def test_ambiguous_context_is_blocked() -> None:
    decision = _decision(relation="ambiguous")
    gate = build_planner_gate(
        decision=decision,
        shadow_plan=_shadow(),
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert "blocked_context_relation:ambiguous" in gate.blockers


def test_action_followup_context_is_blocked() -> None:
    decision = _decision(task_family="runtime_status", primary_task_type="status_query", node="sql", tool="sql_db_query", relation="action_followup")
    shadow = _shadow(node="sql", tool="sql_db_query")

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert "blocked_context_relation:action_followup" in gate.blockers


def test_stale_workorder_context_is_blocked() -> None:
    decision = _decision(
        task_family="action_or_workorder",
        primary_task_type="action_request",
        node="workorder_decision",
        tool="save_report",
        goals=[{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}],
    )
    decision.resolved_context = {"relation_to_previous": "action_followup", "stale_evidence": True}
    shadow = _shadow(node="workorder_decision", tool="save_report", output="workorder_decision")

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert "stale_workorder_not_migrated" in gate.blockers
    assert "action_or_workorder_not_migrated" in gate.blockers


def test_explicit_device_switch_reusing_old_artifact_is_blocked() -> None:
    decision = _decision(task_family="runtime_status", primary_task_type="status_query", node="sql", tool="sql_db_query", relation="correction")
    decision.resolved_context = {"relation_to_previous": "correction", "referenced_artifact_id": "artifact.old"}
    shadow = _shadow(node="sql", tool="sql_db_query")

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert "explicit_device_switch_reuses_artifact" in gate.blockers


def test_needs_review_or_critical_diff_is_blocked() -> None:
    decision = _decision()
    shadow = _shadow()

    needs_review = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "needs_review", "severity": "warning", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )
    critical = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "unsafe_mismatch", "severity": "critical", "counters": {"critical_count": 1}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert needs_review.selected_execution_source == "legacy_policy"
    assert "diff_status_not_allowed" in needs_review.blockers
    assert critical.selected_execution_source == "legacy_policy"
    assert "critical_diff_present" in critical.blockers


def test_shadow_authorized_extra_tool_is_blocked() -> None:
    decision = _decision()
    shadow = _shadow(tool="save_report")

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=shadow,
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert gate.selected_execution_source == "legacy_policy"
    assert "tool_scope_violation" in gate.blockers


def test_no_auth_or_unauthorized_inheritance_is_blocked() -> None:
    missing = _decision()
    missing.authorization = {}
    missing_gate = build_planner_gate(
        decision=missing,
        shadow_plan=_shadow(),
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    decision = _decision()
    decision.authorization = {"mode": "deny", "denied_reason_code": "asset_out_of_scope"}

    gate = build_planner_gate(
        decision=decision,
        shadow_plan=_shadow(),
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": False},
    )

    assert missing_gate.selected_execution_source == "legacy_policy"
    assert "unauthorized_or_missing_auth_context" in missing_gate.blockers
    assert gate.selected_execution_source == "legacy_policy"
    assert "unauthorized_or_missing_auth_context" in gate.blockers
