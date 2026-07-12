from __future__ import annotations

from fault_diagnosis.agent.skills import SkillRegistry


def _skills():
    return SkillRegistry().discover()


def test_fault_code_explain_contract_requires_exact_manual_source() -> None:
    skill = _skills()["fault_code_explain"]
    assert {"manual_reference", "exact_code_match", "source_file", "source_page"} <= set(skill.evidence_policy.required)
    assert {"report", "workorder", "device_action"} <= set(skill.safety_contract.forbidden_actions)
    assert {"current_realtime_status", "alarm_currently_active"} <= set(skill.output_contract.forbidden_claims)


def test_runtime_status_contract_requires_window_and_freshness() -> None:
    skill = _skills()["runtime_status"]
    assert skill.risk_level == "medium"
    assert {
        "asset_identity",
        "data_source",
        "sample_window",
        "latest_sample_time",
        "row_count",
        "freshness",
        "runtime_status",
    } <= set(skill.evidence_policy.required)
    assert {"data_window", "freshness_disclosure"} <= set(skill.output_contract.required_fields)


def test_alarm_triage_contract_requires_sql_and_rag_with_partial_disclosure() -> None:
    skill = _skills()["alarm_triage"]
    assert {"sql_runtime_evidence", "rag_manual_evidence"} <= set(skill.evidence_policy.required)
    assert skill.evidence_policy.missing_evidence_behavior == "partial_disclose"
    assert {
        "current_alarm_presence",
        "event_continuity",
        "manual_reaction",
        "severity",
        "recommendation",
        "unknowns",
        "next_steps",
    } <= set(skill.output_contract.required_fields)


def test_root_cause_contract_is_high_risk_candidate_only() -> None:
    skill = _skills()["root_cause"]
    assert skill.risk_level == "high"
    assert skill.output_contract.variants == ["root_cause_candidates"]
    assert "root_cause_candidates" in skill.output_contract.required_fields
    assert "single_unsupported_root_cause" in skill.output_contract.forbidden_claims


def test_report_generation_contract_controls_source_and_staleness() -> None:
    skill = _skills()["report_generation"]
    assert {"report_source_type", "reportable_artifact", "source_artifact_status"} <= set(skill.slot_policy.required)
    assert skill.evidence_policy.freshness_policy == "refresh_or_disclose"
    assert "freshness_disclosure" in skill.output_contract.required_fields


def test_workorder_contract_exposes_proposal_only() -> None:
    skill = _skills()["workorder_decision"]
    assert skill.allowed_tools == ["workorder.propose_draft"]
    assert "workorder.create" not in skill.allowed_tools
    assert skill.safety_contract.draft_only is True
    assert skill.safety_contract.manual_confirmation_required is True
    assert {"dispatch", "assign", "execute", "close", "device_action"} <= set(skill.safety_contract.forbidden_actions)
    assert skill.evidence_policy.freshness_policy == "refresh_data_first_or_disclose_stale"
