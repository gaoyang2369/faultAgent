from __future__ import annotations

from fault_diagnosis.agent import (
    AgentEngineV2,
    ContextFrame,
    IntentFrameBuilder,
    RewriteFrameBuilder,
    SkillRouter,
)
from fault_diagnosis.domain.security.permissions import build_auth_context


def _route(message: str, context: ContextFrame | None = None):
    frame = context or ContextFrame()
    intent = IntentFrameBuilder().build(message)
    rewrite = RewriteFrameBuilder().build(message, intent_frame=intent, context_frame=frame)
    return SkillRouter().route(intent_frame=intent, rewrite_frame=rewrite, context_frame=frame)


def _engineer():
    return build_auth_context(
        user_id="engineer_01",
        role="engineer",
        asset_scope=["J1号机"],
        table_scope=["real_data_01"],
    )


def test_fault_code_plus_runtime_promotes_alarm_triage() -> None:
    route = _route("A07089 是什么意思，J1 当前状态怎么样")
    assert route.primary_skill == "alarm_triage"
    assert {"fault_code_explain", "runtime_status", "alarm_triage"} <= set(route.selected_skills)


def test_alarm_triage_remains_primary_and_workorder_is_last() -> None:
    route = _route("A07089 是什么？现在 J1 还故障吗？要不要工单？")
    assert route.primary_skill == "alarm_triage"
    assert route.selected_skills[-1] == "workorder_decision"

    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="A07089 是什么？现在 J1 还故障吗？要不要工单？",
        auth_context=_engineer(),
    )
    node_types = [node.node_type for node in snapshot.execution_plan.nodes]
    assert node_types.count("sql") == 1
    assert node_types.count("rag") == 1
    assert node_types.index("workorder") > node_types.index("analysis")


def test_report_precedes_workorder_and_supplies_dependency() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="J1 生成报告，然后看看是否需要工单",
        auth_context=_engineer(),
    )
    assert snapshot.skill_route.primary_skill == "report_generation"
    assert snapshot.skill_route.selected_skills[-1] == "workorder_decision"
    edges = {(edge.get("from"), edge.get("to")) for edge in snapshot.execution_plan.edges}
    assert ("report_1", "workorder_1") in edges


def test_clarification_blocks_composition_for_ambiguous_context() -> None:
    context = ContextFrame(
        relation_to_previous="ambiguous",
        missing_context=["请确认目标设备。"],
    )
    route = _route("它严重吗，还要生成工单吗", context)
    assert route.primary_skill == "clarification"
    assert route.selected_skills == ["clarification"]
    assert "workorder_decision" in route.blocked_skills
