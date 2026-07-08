from __future__ import annotations

from fault_diagnosis.agent import (
    AgentEngineV2,
    ContextFrame,
    ExecutionPlan,
    IntentFrameBuilder,
    PlanCompiler,
    PlanValidator,
    RewriteFrameBuilder,
    SkillRouter,
)
from fault_diagnosis.agent.planning.plan_diff import diff_plans
from fault_diagnosis.domain.security.permissions import build_auth_context


def _route(raw_message: str):
    intent = IntentFrameBuilder().build(raw_message)
    context = ContextFrame()
    rewrite = RewriteFrameBuilder().build(raw_message, intent_frame=intent, context_frame=context)
    route = SkillRouter().route(intent_frame=intent, rewrite_frame=rewrite, context_frame=context)
    return intent, context, route


def _engineer(*, assets: list[str] | None = None, tables: list[str] | None = None):
    return build_auth_context(
        user_id="engineer_01",
        role="engineer",
        asset_scope=assets or ["J1号机"],
        table_scope=tables or ["real_data_01"],
    )


def test_compiler_builds_runtime_status_candidate_plan() -> None:
    intent, context, route = _route("J1 当前运行状态怎么样")
    plan = PlanCompiler().compile(skill_route=route, intent_frame=intent, context_frame=context)

    assert route.primary_skill == "runtime_status"
    assert [node["node_type"] for node in plan.nodes] == ["sql"]
    assert plan.allowed_tools == ["sql.read"]
    assert "latest_runtime_status" in plan.required_evidence
    assert plan.nodes[0]["requested_tables"] == ["real_data_01"]


def test_guest_blocks_report_root_cause_and_workorder_plans() -> None:
    cases = [
        ("基于刚才结果生成报告", "report_generation"),
        ("诊断 J1 A07089 的根因", "root_cause"),
        ("判断 J1 A07089 是否需要工单草稿", "workorder_decision"),
    ]

    for message, skill in cases:
        snapshot = AgentEngineV2().plan_only(raw_message=message, auth_context=build_auth_context(role="guest"))

        assert snapshot.skill_route.primary_skill == skill
        assert snapshot.status == "blocked"
        assert snapshot.output_frame.guardrail_result["authorization"]["mode"] == "deny"
        assert "report.write_draft" not in snapshot.execution_plan.allowed_tools
        assert "workorder.create" not in snapshot.execution_plan.allowed_tools


def test_engineer_scope_checks_assets_and_tables() -> None:
    denied_asset = AgentEngineV2().plan_only(
        raw_message="查询 J2 当前运行状态",
        auth_context=_engineer(assets=["J1号机"], tables=["real_data_01", "real_data_02"]),
    )
    denied_table = AgentEngineV2().plan_only(
        raw_message="查询 J2 当前运行状态",
        auth_context=_engineer(assets=["J2号机"], tables=["real_data_01"]),
    )
    allowed = AgentEngineV2().plan_only(
        raw_message="查询 J2 当前运行状态",
        auth_context=_engineer(assets=["J2号机"], tables=["real_data_02"]),
    )

    assert denied_asset.status == "blocked"
    assert any(issue["code"] == "asset_out_of_scope" for issue in denied_asset.trace["validation"]["issues"])
    assert denied_table.status == "blocked"
    assert any(issue["code"] == "table_out_of_scope" for issue in denied_table.trace["validation"]["issues"])
    assert allowed.status == "validated"
    assert allowed.execution_plan.allowed_tools == ["sql.read"]
    assert allowed.execution_plan.approval_requirements == []


def test_workorder_and_device_action_generate_approval_requirements() -> None:
    workorder = AgentEngineV2().plan_only(
        raw_message="判断 J1 A07089 是否需要工单草稿",
        auth_context=_engineer(),
    )
    assert any(item["type"] == "workorder_draft" for item in workorder.execution_plan.approval_requirements)

    intent, _context, route = _route("判断 J1 A07089 是否需要工单草稿")
    dangerous = ExecutionPlan(
        plan_id="candidate.dangerous",
        plan_version="v2.candidate.test",
        goals=[{"goal_id": "goal_device_action", "skill": "workorder_decision"}],
        nodes=[{"node_id": "device_action_1", "node_type": "device_action", "required_tools": ["device_control.write"]}],
        allowed_tools=["device_control.write"],
        forbidden_tools=[],
        expected_outputs=["workorder_draft"],
        risk_level="critical",
    )

    result = PlanValidator().validate(
        candidate_plan=dangerous,
        skill_route=route,
        intent_frame=intent,
        auth_context=_engineer(),
    )

    assert result.status == "blocked"
    assert result.validated_plan.plan_version.endswith(".blocked")
    assert "device_control.write" in result.removed_tools
    assert any(item["type"] == "device_action" and item["allowed_next_step"] == "deny" for item in result.approval_requirements)


def test_llm_candidate_dangerous_tools_are_removed_and_blocked() -> None:
    intent, _context, route = _route("J1 当前运行状态怎么样")
    candidate = ExecutionPlan(
        plan_id="candidate.llm",
        plan_version="v2.candidate.llm",
        goals=[{"goal_id": "goal_runtime", "skill": "runtime_status"}],
        nodes=[{"node_id": "sql_1", "node_type": "sql", "required_tools": ["sql.read", "sql.write"]}],
        allowed_tools=["sql.read", "sql.write"],
        risk_level="critical",
    )

    result = PlanValidator().validate(
        candidate_plan=candidate,
        skill_route=route,
        intent_frame=intent,
        auth_context=_engineer(),
    )

    assert result.status == "blocked"
    assert result.validated_plan.allowed_tools == ["sql.read"]
    assert "sql.write" in result.removed_tools
    assert any(issue.code == "forbidden_tool_requested" for issue in result.issues)


def test_plan_diff_compares_legacy_and_v2_surfaces() -> None:
    legacy = {
        "enabled_nodes": {"sql": True, "knowledge": False},
        "runtime_tools": ["sql_db_query"],
        "evidence_gaps": {"required_evidence": ["latest_runtime_status"]},
        "requested_output": "answer",
    }
    v2 = ExecutionPlan(
        nodes=[{"node_id": "sql_1", "node_type": "sql"}],
        allowed_tools=["sql.read"],
        required_evidence=["latest_runtime_status"],
        expected_outputs=["status_brief"],
    )

    diff = diff_plans(legacy, v2)

    assert diff["legacy"]["tools"] == ["sql.read"]
    assert diff["v2"]["tools"] == ["sql.read"]
    assert diff["added"]["nodes"] == []
    assert diff["removed"]["nodes"] == []
    assert diff["changed"]["expected_outputs"] == ["answer", "status_brief"]
