from __future__ import annotations

from fault_diagnosis.single_agent.planning import build_planning_diff, summarize_planning_diff


def _shadow(
    *,
    nodes: dict[str, str] | None = None,
    candidate_tools: list[str] | None = None,
    authorized_tools: list[str] | None = None,
    refresh_required: bool = False,
    disclosures: list[str] | None = None,
    expected_output: str = "answer",
    workorder_boundary: str | None = None,
    report_boundary: str | None = None,
    guardrails: list[str] | None = None,
) -> dict:
    return {
        "planner_mode": "shadow",
        "nodes": [
            {"node": node, "desired_state": state}
            for node, state in (nodes or {}).items()
        ],
        "tool_plan": {
            "candidate_tools": candidate_tools or [],
            "authorized_runtime_tools": authorized_tools or [],
        },
        "evidence_plan": {
            "required_evidence": [],
            "refresh_required": refresh_required,
            "disclosure_required": disclosures or [],
            "stale_evidence": ["referenced_artifact"] if refresh_required else [],
        },
        "output_plan": {
            "expected_output": expected_output,
            "required_disclosures": disclosures or [],
            "workorder_boundary": workorder_boundary,
            "report_boundary": report_boundary,
            "final_answer_guardrails": guardrails or [],
        },
        "legacy_projection": {},
    }


def _legacy(**overrides) -> dict:
    payload = {
        "primary_task_type": "status_query",
        "intent_stack": ["check_current_status"],
        "enabled_nodes": {"sql": True},
        "runtime_tools": ["sql_db_query"],
        "evidence_mode": "collect_new",
        "should_refresh_runtime_data": False,
        "requested_output": "answer",
        "workflow_policy": {"evidence_requirements": {}, "guardrails": []},
        "resolved_context": {},
        "task_family": "runtime_status",
    }
    payload.update(overrides)
    return payload


def test_planning_diff_exact_match_is_aligned() -> None:
    diff = build_planning_diff(
        _legacy(),
        _shadow(nodes={"sql": "enabled"}, candidate_tools=["sql_db_query"], authorized_tools=["sql_db_query"]),
    )

    assert diff.overall_status == "aligned"
    assert diff.severity == "none"


def test_shadow_candidate_only_tool_is_acceptable_info() -> None:
    diff = build_planning_diff(
        _legacy(runtime_tools=["query_knowledge_base"], enabled_nodes={"knowledge": True}),
        _shadow(
            nodes={"knowledge": "enabled", "sql": "enabled"},
            candidate_tools=["query_knowledge_base", "sql_db_query"],
            authorized_tools=["query_knowledge_base"],
        ),
    )

    assert diff.overall_status == "acceptable_diff"
    assert any(item.diff_type == "shadow_candidate_only" and item.severity == "info" for item in diff.tool_diffs)
    assert summarize_planning_diff(diff)["critical_count"] == 0


def test_shadow_authorized_tool_over_legacy_is_critical() -> None:
    diff = build_planning_diff(
        _legacy(runtime_tools=["query_knowledge_base"], enabled_nodes={"knowledge": True}),
        _shadow(
            nodes={"knowledge": "enabled", "sql": "enabled"},
            candidate_tools=["query_knowledge_base", "sql_db_query"],
            authorized_tools=["query_knowledge_base", "sql_db_query"],
        ),
    )

    assert diff.overall_status == "unsafe_mismatch"
    assert diff.severity == "critical"
    assert any(item.diff_type == "unauthorized_tool" for item in diff.safety_diffs)


def test_legacy_guardrail_node_skipped_by_shadow_is_critical() -> None:
    diff = build_planning_diff(
        _legacy(enabled_nodes={"permission_check": True}, runtime_tools=[]),
        _shadow(nodes={"permission_check": "skipped"}),
    )

    assert diff.overall_status == "unsafe_mismatch"
    assert any(item.node == "permission_check" and item.severity == "critical" for item in diff.node_diffs)


def test_stale_workorder_refresh_required_has_no_critical() -> None:
    diff = build_planning_diff(
        _legacy(
            enabled_nodes={"workorder_decision": True},
            runtime_tools=[],
            should_refresh_runtime_data=True,
            resolved_context={"stale_evidence": True},
            task_family="action_or_workorder",
        ),
        _shadow(
            nodes={"workorder_decision": "enabled"},
            refresh_required=True,
            disclosures=["evidence_stale"],
            expected_output="workorder_decision",
            workorder_boundary="only_draft_or_confirmation",
        ),
    )

    assert diff.overall_status in {"acceptable_diff", "needs_review"}
    assert summarize_planning_diff(diff)["critical_count"] == 0


def test_stale_workorder_without_refresh_or_disclosure_is_critical() -> None:
    diff = build_planning_diff(
        _legacy(
            enabled_nodes={"workorder_decision": True},
            runtime_tools=[],
            should_refresh_runtime_data=True,
            resolved_context={"stale_evidence": True},
            task_family="action_or_workorder",
        ),
        _shadow(
            nodes={"workorder_decision": "enabled"},
            expected_output="workorder_decision",
            workorder_boundary="only_draft_or_confirmation",
        ),
    )

    assert diff.overall_status == "unsafe_mismatch"
    assert any(item.diff_type == "stale_refresh_mismatch" for item in diff.evidence_diffs)


def test_workorder_executed_boundary_is_critical() -> None:
    diff = build_planning_diff(
        _legacy(enabled_nodes={"workorder_decision": True}, runtime_tools=[], task_family="action_or_workorder"),
        _shadow(
            nodes={"workorder_decision": "enabled"},
            expected_output="workorder_decision",
            workorder_boundary="executed",
        ),
    )

    assert diff.overall_status == "unsafe_mismatch"
    assert any(item.diff_type == "workorder_boundary_mismatch" for item in diff.output_diffs)


def test_missing_legacy_fields_returns_needs_review_warning() -> None:
    diff = build_planning_diff({}, _shadow(nodes={"knowledge": "enabled"}, candidate_tools=["query_knowledge_base"]))

    assert diff.overall_status == "needs_review"
    assert diff.severity == "warning"


def test_explicit_device_switch_without_old_artifact_has_no_critical() -> None:
    diff = build_planning_diff(
        _legacy(
            enabled_nodes={"sql": True},
            runtime_tools=["sql_db_query"],
            resolved_context={"relation_to_previous": "new_case", "referenced_artifact_id": None},
        ),
        _shadow(nodes={"sql": "enabled"}, candidate_tools=["sql_db_query"], authorized_tools=["sql_db_query"]),
    )

    assert summarize_planning_diff(diff)["critical_count"] == 0


def test_unauthorized_artifact_reference_is_critical() -> None:
    diff = build_planning_diff(
        _legacy(
            enabled_nodes={"report": True},
            runtime_tools=["save_report"],
            resolved_context={"unauthorized_artifact_reference": True},
            task_family="reporting",
            requested_output="report",
        ),
        _shadow(
            nodes={"report": "enabled"},
            candidate_tools=["save_report"],
            authorized_tools=["save_report"],
            expected_output="report",
            report_boundary="reuse_previous_artifact_if_authorized",
        ),
    )

    assert diff.overall_status == "unsafe_mismatch"
    assert any(item.diff_type == "unauthorized_reference" for item in diff.safety_diffs)
