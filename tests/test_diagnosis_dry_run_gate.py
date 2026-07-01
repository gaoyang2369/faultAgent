from __future__ import annotations

from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.planning import apply_planner_gate_to_decision, build_planner_gate


def _decision(
    *,
    primary_task_type: str = "alarm_triage",
    enabled_nodes: dict[str, bool] | None = None,
    runtime_tools: list[str] | None = None,
    objects: dict | None = None,
    resolved_context: dict | None = None,
    goals: list[dict] | None = None,
    task_family: str = "diagnosis",
) -> SingleAgentDecision:
    return SingleAgentDecision(
        primary_task_type=primary_task_type,
        task_family=task_family,
        enabled_nodes=enabled_nodes or {"sql": True, "knowledge": True, "analysis": True},
        runtime_tools=runtime_tools or ["sql_db_query", "query_knowledge_base"],
        objects=objects or {"device_ids": ["J1"], "alarm_codes": ["A07089"]},
        intent_stack=["explain_alarm_code", "check_current_status", "resolution_recommendation"],
        goals=goals or [{"goal_type": "check_runtime_status", "risk_level": "read_only"}],
        goal_set={"goals": goals or [{"goal_type": "check_runtime_status", "risk_level": "read_only"}]},
        resolved_context=resolved_context or {"relation_to_previous": "new_case", "stale_evidence": False},
        authorization={"mode": "allow"},
    )


def _shadow(
    *,
    nodes: list[str] | None = None,
    tools: list[str] | None = None,
    disclosures: list[str] | None = None,
    missing_evidence: list[str] | None = None,
) -> dict:
    nodes = nodes or ["sql", "knowledge", "analysis"]
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
        "output_plan": {"expected_output": "answer", "required_disclosures": disclosures or []},
    }


def _gate(decision: SingleAgentDecision, shadow: dict | None = None, *, dry_run: bool = True) -> object:
    return build_planner_gate(
        decision=decision,
        shadow_plan=shadow or _shadow(),
        planning_diff={"overall_status": "aligned", "severity": "none", "counters": {}},
        config_overrides={"enabled": True, "dry_run": dry_run},
    )


def _readiness(gate: object) -> dict:
    return gate.safety_summary["diagnosis_readiness"]


def test_diagnosis_dry_run_default_never_active_and_preserves_execution() -> None:
    decision = _decision()
    before_nodes = dict(decision.enabled_nodes)
    before_tools = list(decision.runtime_tools)

    gate = _gate(decision)
    apply_planner_gate_to_decision(decision, gate)

    assert gate.mode == "dry_run"
    assert gate.selected_execution_source == "legacy_policy"
    assert _readiness(gate)["ready_for_active"] is False
    assert decision.enabled_nodes == before_nodes
    assert decision.runtime_tools == before_tools


def test_diagnosis_global_active_still_cannot_select_planner_gated() -> None:
    decision = _decision()
    gate = _gate(decision, dry_run=False)

    assert gate.mode == "dry_run"
    assert gate.selected_execution_source == "legacy_policy"
    assert "diagnosis_dry_run_only" in gate.blockers
    assert "diagnosis_active_not_enabled" in gate.blockers


def test_evidence_complete_alarm_triage_is_only_limited_active_candidate() -> None:
    gate = _gate(_decision(primary_task_type="alarm_triage"))
    readiness = _readiness(gate)

    assert readiness["evidence_complete"] is True
    assert readiness["ready_for_active"] is False
    assert readiness["recommended_next_phase"] == "candidate_for_limited_active"


def test_rca_and_health_default_to_more_eval_or_keep_legacy() -> None:
    rca = _gate(_decision(primary_task_type="root_cause_analysis"))
    health = _gate(_decision(primary_task_type="health_assessment", objects={"device_ids": ["J1"], "alarm_codes": []}))

    assert _readiness(rca)["recommended_next_phase"] in {"more_eval", "keep_legacy"}
    assert _readiness(health)["recommended_next_phase"] in {"more_eval", "keep_legacy"}
    assert _readiness(rca)["recommended_next_phase"] != "candidate_for_limited_active"
    assert _readiness(health)["recommended_next_phase"] != "candidate_for_limited_active"


def test_stale_without_disclosure_is_blocked() -> None:
    decision = _decision(resolved_context={"relation_to_previous": "continuation", "stale_evidence": True})
    gate = _gate(decision, _shadow(disclosures=[]))

    assert "stale_evidence_without_disclosure" in _readiness(gate)["blocked_reasons"]
    assert _readiness(gate)["ready_for_active"] is False


def test_missing_runtime_status_is_blocked() -> None:
    decision = _decision(enabled_nodes={"knowledge": True, "analysis": True}, runtime_tools=["query_knowledge_base"])
    gate = _gate(decision, _shadow(nodes=["knowledge", "analysis"], tools=["query_knowledge_base"]))

    assert "missing_runtime_status" in _readiness(gate)["blocked_reasons"]


def test_missing_device_is_blocked() -> None:
    decision = _decision(objects={"device_ids": [], "alarm_codes": ["A07089"]})
    gate = _gate(decision)

    assert "missing_device" in _readiness(gate)["blocked_reasons"]


def test_unauthorized_artifact_reference_is_blocked_without_detail_leak() -> None:
    decision = _decision(resolved_context={"relation_to_previous": "continuation", "referenced_artifact_id": "artifact.secret"})
    decision.authorization = {"mode": "deny", "denied_reason_code": "asset_out_of_scope"}
    gate = _gate(decision)
    readiness = _readiness(gate)

    assert "unauthorized_inherited_artifact" in readiness["blocked_reasons"]
    assert "artifact.secret" not in str(readiness)


def test_action_workorder_remains_blocked_and_has_no_diagnosis_active() -> None:
    decision = _decision(
        task_family="action_or_workorder",
        primary_task_type="action_request",
        goals=[{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}],
    )
    gate = _gate(decision, _shadow(nodes=["analysis"], tools=[]))

    assert gate.selected_execution_source == "legacy_policy"
    assert "action_or_workorder_not_migrated" in gate.blockers
    assert "diagnosis_readiness" not in gate.safety_summary
