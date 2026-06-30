from __future__ import annotations

from fault_diagnosis.single_agent.planning import build_planning_diff, build_planning_input, build_shadow_plan, summarize_planning_diff


def _goal(goal_type: str, *, status: str = "ready", goal_id: str | None = None) -> dict:
    return {
        "goal_id": goal_id or f"goal_{goal_type}",
        "goal_type": goal_type,
        "status": status,
    }


def _plan(
    *,
    goals: list[dict],
    resolved_context: dict | None = None,
    task_family: str = "diagnosis",
    legacy_runtime_tools: list[str] | None = None,
    legacy_enabled_nodes: dict[str, bool] | None = None,
):
    planning_input = build_planning_input(
        message="test",
        request_payload_summary={},
        auth_summary={"role": "engineer"},
        resolved_context=resolved_context or {},
        goal_set={"goals": goals},
        primary_task_type="fault_diagnosis",
        intent_stack=["fault_diagnosis"],
        task_family=task_family,
        referenced_artifact_id=(resolved_context or {}).get("referenced_artifact_id"),
    )
    return build_shadow_plan(
        planning_input,
        legacy_plan={
            "legacy_enabled_nodes": legacy_enabled_nodes or {"sql": True, "knowledge": True, "analysis": True},
            "legacy_runtime_tools": legacy_runtime_tools or ["sql_db_query_checker", "sql_db_query", "query_knowledge_base"],
            "legacy_requested_output": "answer",
            "legacy_evidence_mode": "collect_new",
            "legacy_should_refresh_runtime_data": False,
        },
    )


def _enabled_nodes(plan) -> set[str]:
    return {node.node for node in plan.nodes if node.desired_state == "enabled"}


def test_composite_diagnosis_shadow_nodes_and_tools_do_not_mutate_legacy_runtime_tools() -> None:
    legacy_tools = ["sql_db_query", "query_knowledge_base"]
    plan = _plan(
        goals=[
            _goal("explain_fault_code"),
            _goal("check_runtime_status"),
            _goal("diagnose_fault"),
            _goal("recommend_resolution"),
        ],
        legacy_runtime_tools=legacy_tools,
    )

    assert {"knowledge", "sql", "analysis", "resolution_recommendation"}.issubset(_enabled_nodes(plan))
    assert set(plan.tool_plan.authorized_runtime_tools) == {"sql_db_query", "query_knowledge_base"}
    assert legacy_tools == ["sql_db_query", "query_knowledge_base"]


def test_report_handoff_reuses_referenced_artifact() -> None:
    plan = _plan(
        goals=[_goal("generate_report")],
        resolved_context={
            "relation_to_previous": "report_handoff",
            "referenced_artifact_id": "artifact.report",
            "inherited_slots": {"evidence_bundle": "eb.report"},
        },
        task_family="reporting",
        legacy_runtime_tools=["save_report"],
        legacy_enabled_nodes={"report": True},
    )

    assert "report" in _enabled_nodes(plan)
    assert "artifact.report" in plan.evidence_plan.reusable_evidence
    assert "eb.report" in plan.evidence_plan.reusable_evidence
    assert plan.output_plan.expected_output == "report"


def test_stale_workorder_requires_refresh_and_draft_boundary() -> None:
    plan = _plan(
        goals=[_goal("decide_workorder")],
        resolved_context={
            "relation_to_previous": "action_followup",
            "referenced_artifact_id": "artifact.stale",
            "stale_evidence": True,
            "inherited_slots": {"evidence_bundle": "eb.stale"},
        },
        task_family="action_or_workorder",
        legacy_runtime_tools=[],
        legacy_enabled_nodes={"workorder_decision": True, "sql": False},
    )

    assert plan.evidence_plan.refresh_required is True
    assert "evidence_stale" in plan.output_plan.required_disclosures
    assert plan.output_plan.workorder_boundary == "only_draft_or_confirmation"
    assert "workorder_decision" in _enabled_nodes(plan)
    text = " ".join([plan.output_plan.workorder_boundary or "", *plan.output_plan.final_answer_guardrails])
    assert all(word not in text for word in ("executed", "dispatched", "reset", "applied"))


def test_ambiguous_reference_blocks_business_nodes_and_clarifies() -> None:
    plan = _plan(
        goals=[_goal("clarify_missing_context"), _goal("assess_severity", status="blocked")],
        resolved_context={"relation_to_previous": "ambiguous", "missing_context": ["请确认设备"]},
        task_family="diagnosis",
    )

    blocked = {node.node for node in plan.nodes if node.desired_state == "blocked"}
    assert "analysis" in blocked or "business_goal" in blocked
    assert plan.output_plan.expected_output == "clarification"


def test_unauthorized_inheritance_does_not_directly_enable_report_or_workorder_reuse() -> None:
    plan = _plan(
        goals=[_goal("generate_report"), _goal("decide_workorder")],
        resolved_context={
            "relation_to_previous": "ambiguous",
            "inherited_slots": {},
            "missing_context": ["授权范围不足，不能继承上一轮结果"],
            "context_resolution_reason": "授权范围不足",
        },
        task_family="reporting",
        legacy_runtime_tools=[],
        legacy_enabled_nodes={},
    )

    blocked = {node.node for node in plan.nodes if node.desired_state == "blocked"}
    assert "report" in blocked
    assert "workorder_decision" in blocked
    assert plan.output_plan.expected_output == "clarification"


def test_direct_meta_skips_tool_nodes() -> None:
    plan = _plan(goals=[], task_family="meta", legacy_runtime_tools=[], legacy_enabled_nodes={})

    assert "final_answer" in {node.node for node in plan.nodes}
    assert plan.output_plan.expected_output == "answer"
    assert plan.tool_plan.authorized_runtime_tools == []


def test_candidate_tools_can_exceed_authorized_but_never_legacy_runtime_tools() -> None:
    plan = _plan(
        goals=[_goal("diagnose_fault"), _goal("generate_report")],
        task_family="diagnosis",
        legacy_runtime_tools=["query_knowledge_base"],
    )

    assert set(plan.tool_plan.candidate_tools).issuperset({"sql_db_query", "query_knowledge_base", "save_report"})
    assert plan.tool_plan.authorized_runtime_tools == ["query_knowledge_base"]
    assert all(tool in {"query_knowledge_base"} for tool in plan.tool_plan.authorized_runtime_tools)
    diff = build_planning_diff(
        {
            "enabled_nodes": {"knowledge": True},
            "runtime_tools": ["query_knowledge_base"],
            "evidence_mode": "collect_new",
            "should_refresh_runtime_data": False,
            "requested_output": "answer",
        },
        plan,
    )
    summary = summarize_planning_diff(diff)
    assert summary["critical_count"] == 0
    assert "node_diffs" not in summary
