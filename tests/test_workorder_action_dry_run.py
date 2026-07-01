from types import SimpleNamespace

from fault_diagnosis.single_agent.planning import (
    build_planner_gate,
    build_workorder_action_readiness,
    summarize_planner_gate,
)


def _decision(**overrides):
    data = {
        "primary_task_type": "action_request",
        "task_family": "action_or_workorder",
        "enabled_nodes": {
            "permission_check": True,
            "risk_check": True,
            "analysis": True,
            "workorder_decision": True,
            "output_guardrail": True,
            "audit_log": True,
        },
        "runtime_tools": [],
        "goals": [{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}],
        "goal_set": {"goals": [{"goal_type": "decide_workorder", "risk_level": "requires_confirmation"}]},
        "resolved_context": {"relation_to_previous": "action_followup", "stale_evidence": False},
        "authorization": {"mode": "allow"},
        "missing_slots": [],
        "missing_or_stale_evidence": [],
        "satisfied_evidence": [
            "diagnosis_summary",
            "severity_or_status_level",
            "key_evidence",
            "recommended_action_policy",
        ],
        "intent_stack": ["workorder_decision"],
        "action_type": None,
        "action_target": None,
        "risk_level": "requires_confirmation",
        "should_refresh_runtime_data": False,
        "user_goal": "判断是否需要生成工单",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _shadow(*, nodes=None, expected_output="workorder_decision", output_text="ask_confirmation"):
    nodes = nodes or ["permission_check", "risk_check", "analysis", "workorder_decision", "output_guardrail", "audit_log"]
    return {
        "nodes": [{"node": node, "enabled": True} for node in nodes],
        "tool_plan": {"authorized_runtime_tools": []},
        "evidence_plan": {"required_evidence": ["diagnosis_summary"], "missing_evidence": []},
        "output_plan": {"expected_output": expected_output, "answer_style": output_text, "required_disclosures": []},
    }


def _diff(status="acceptable_diff", severity="warning", critical=0):
    return {"overall_status": status, "severity": severity, "counters": {"critical_count": critical}}


def test_workorder_decision_is_dry_run_and_never_active() -> None:
    decision = _decision()
    readiness = build_workorder_action_readiness(decision=decision, shadow_plan=_shadow(), planning_diff=_diff())

    assert readiness.ready_for_active is False
    assert readiness.dry_run_only is True
    assert readiness.action_type == "workorder_decision"
    assert readiness.requires_human_confirmation is True
    assert readiness.recommended_next_phase == "candidate_for_draft_only"

    gate = build_planner_gate(decision=decision, shadow_plan=_shadow(), planning_diff=_diff(), config_overrides={"enabled": True, "dry_run": False})
    summary = summarize_planner_gate(gate)
    assert gate.selected_execution_source == "legacy_policy"
    assert gate.fallback_to_legacy is True
    assert summary["workorder_action_readiness"]["ready_for_active"] is False


def test_workorder_draft_dry_run_does_not_dispatch() -> None:
    decision = _decision(
        primary_task_type="action_request",
        goals=[{"goal_type": "create_workorder_draft", "risk_level": "requires_confirmation"}],
        goal_set={"goals": [{"goal_type": "create_workorder_draft", "risk_level": "requires_confirmation"}]},
        user_goal="生成工单草稿",
    )
    readiness = build_workorder_action_readiness(
        decision=decision,
        shadow_plan=_shadow(expected_output="workorder_draft"),
        planning_diff=_diff(),
    )

    assert readiness.action_type == "workorder_draft"
    assert readiness.ready_for_active is False
    assert "workorder_action_dry_run_only" in readiness.blockers


def test_stale_workorder_requires_refresh_or_disclosure() -> None:
    decision = _decision(
        resolved_context={"relation_to_previous": "action_followup", "stale_evidence": True},
        missing_or_stale_evidence=["latest_realtime_status"],
    )
    readiness = build_workorder_action_readiness(decision=decision, shadow_plan=_shadow(), planning_diff=_diff())

    assert readiness.stale_refresh_required is True
    assert "stale_refresh_or_disclosure_required" in readiness.blockers
    assert "latest_realtime_status" in readiness.missing_critical_evidence


def test_device_action_is_blocked() -> None:
    decision = _decision(
        action_type="reset",
        action_target="J1",
        user_goal="复位 J1",
        intent_stack=["device_reset"],
        goals=[],
        goal_set={"goals": []},
    )
    readiness = build_workorder_action_readiness(decision=decision, shadow_plan=_shadow(expected_output="answer"), planning_diff=_diff())

    assert readiness.action_type == "device_action"
    assert readiness.recommended_next_phase == "keep_legacy"
    assert "device_action_not_migrated" in readiness.blockers


def test_unauthorized_and_ambiguous_are_blocked() -> None:
    decision = _decision(
        authorization={"mode": "deny"},
        resolved_context={"relation_to_previous": "ambiguous"},
    )
    readiness = build_workorder_action_readiness(decision=decision, shadow_plan=_shadow(), planning_diff=_diff())

    assert "unauthorized_or_missing_auth_context" in readiness.blockers
    assert "blocked_context_relation:ambiguous" in readiness.blockers


def test_critical_or_needs_review_diff_blocks_high_risk_dry_run_candidate() -> None:
    decision = _decision()

    needs_review = build_workorder_action_readiness(decision=decision, shadow_plan=_shadow(), planning_diff=_diff(status="needs_review"))
    critical = build_workorder_action_readiness(decision=decision, shadow_plan=_shadow(), planning_diff=_diff(critical=1))

    assert "diff_status_not_allowed" in needs_review.blockers
    assert "critical_diff_present" in critical.blockers


def test_unsafe_completed_action_semantics_are_blocked() -> None:
    decision = _decision()
    readiness = build_workorder_action_readiness(
        decision=decision,
        shadow_plan=_shadow(output_text="已执行复位"),
        planning_diff=_diff(),
    )

    assert "unsafe_action_completion_semantics" in readiness.blockers


def test_required_guardrails_cannot_be_removed() -> None:
    decision = _decision()
    readiness = build_workorder_action_readiness(
        decision=decision,
        shadow_plan=_shadow(nodes=["analysis", "workorder_decision"]),
        planning_diff=_diff(),
    )

    assert "permission_check_would_be_removed" in readiness.blockers
    assert "risk_check_would_be_removed" in readiness.blockers
    assert "audit_log_would_be_removed" in readiness.blockers
    assert "output_guardrail_would_be_removed" in readiness.blockers
