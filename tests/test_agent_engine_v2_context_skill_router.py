from __future__ import annotations

from fault_diagnosis.agent_engine import (
    AgentEngineV2,
    ContextFrame,
    ContextFrameAdapter,
    IntentFrameBuilder,
    RewriteFrameBuilder,
    SkillLoader,
    SkillRegistry,
    SkillRouter,
)
from fault_diagnosis.context import ArtifactBackedCaseStore, ContextManager
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.security.permissions import build_auth_context


def _artifact(
    *,
    thread_id: str = "thread.v2.context",
    asset: str = "J1",
    fault_code: str = "A07089",
) -> DiagnosisArtifactEnvelope:
    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
        thread_id=thread_id,
        created_at="2026-06-24T10:00:00",
        request_summary=f"生成 {asset} 运行报告",
        final_answer=f"上一轮报告：{fault_code} 持续出现。",
        report_filename=f"{asset}.html",
        payload={
            "request": {
                "user_message": f"生成 {asset} 运行报告",
                "equipment_hint": asset,
                "fault_code_hint": fault_code,
                "analysis_goal": "运行报告",
            },
            "decision": {
                "objects": {"device_ids": [asset], "alarm_codes": [fault_code]},
                "time_window": {"default_strategy": "最近"},
            },
            "evidence_bundle": {"bundle_id": f"eb_{asset}", "trace_id": f"trace_{asset}"},
            "report_artifact": {
                "success": True,
                "report_filename": f"{asset}.html",
                "report_url": f"/reports/{asset}.html",
                "save_result": f"/reports/{asset}.html",
            },
            "analysis_artifact": {
                "success": True,
                "conclusion": f"{asset} {fault_code} 持续出现",
                "recommendations": ["刷新当前状态后确认是否派发"],
            },
            "operation_report_payload": {
                "asset": asset,
                "status_level": "告警 / 需确认",
                "current_event": f"{fault_code} 持续出现",
                "data_freshness_label": "当前",
                "data_currentness_label": "CURRENT",
                "evidence_summary": [f"{fault_code} 持续出现"],
                "next_action": "刷新当前状态后确认是否派发",
            },
            "workorder_decision": {
                "lifecycle_status": "recommended_draft",
                "need_workorder": True,
                "pending_action": {
                    "action_type": "workorder_draft",
                    "status": "pending",
                    "artifact_id": f"eb_{asset}",
                    "source_diagnosis_artifact_id": f"eb_{asset}",
                    "source_report_artifact_id": f"/reports/{asset}.html",
                    "required_role": "engineer",
                },
            },
        },
        evidence=[],
    )


def _manager_with_artifacts(*artifacts: DiagnosisArtifactEnvelope) -> ContextManager:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    for artifact in artifacts:
        save_thread_artifact(artifact)
    return ContextManager(case_store=ArtifactBackedCaseStore())


def _engineer(asset_scope: list[str] | None = None):
    return build_auth_context(
        user_id="engineer",
        role="engineer",
        asset_scope=asset_scope or ["J1"],
        table_scope=["real_data_01"],
    )


def _route(
    raw_message: str,
    *,
    context_frame: ContextFrame | None = None,
):
    intent = IntentFrameBuilder().build(raw_message)
    rewrite = RewriteFrameBuilder().build(
        raw_message,
        intent_frame=intent,
        context_frame=context_frame or ContextFrame(),
    )
    route = SkillRouter().route(
        intent_frame=intent,
        rewrite_frame=rewrite,
        context_frame=context_frame or ContextFrame(),
    )
    return intent, rewrite, route


def test_report_handoff_inherits_artifact_and_loads_only_report_skill() -> None:
    manager = _manager_with_artifacts(_artifact())
    intent = IntentFrameBuilder().build("基于刚才结果生成报告")
    context = ContextFrameAdapter(manager).resolve(
        thread_id="thread.v2.context",
        raw_message="基于刚才结果生成报告",
        intent_frame=intent,
        auth_context=_engineer(),
    )
    rewrite = RewriteFrameBuilder().build(
        "基于刚才结果生成报告",
        intent_frame=intent,
        context_frame=context,
    )
    route = SkillRouter().route(intent_frame=intent, rewrite_frame=rewrite, context_frame=context)

    assert context.relation_to_previous == "report_handoff"
    assert context.inherited_slots["evidence_bundle"] == "eb_J1"
    assert route.primary_skill == "report_generation"
    assert route.selected_skills == ["report_generation"]
    assert route.skill_inputs["loaded_skill_names"] == ["report_generation"]
    assert all(item.startswith("report_generation/") for item in route.load_set)


def test_ambiguous_context_routes_to_clarification_and_blocks_diagnosis_skills() -> None:
    manager = _manager_with_artifacts(_artifact(asset="J1"), _artifact(asset="J2"))
    intent = IntentFrameBuilder().build("它严重吗")
    context = ContextFrameAdapter(manager).resolve(
        thread_id="thread.v2.context",
        raw_message="它严重吗",
        intent_frame=intent,
        auth_context=_engineer(asset_scope=["J1", "J2"]),
    )
    rewrite = RewriteFrameBuilder().build("它严重吗", intent_frame=intent, context_frame=context)
    route = SkillRouter().route(intent_frame=intent, rewrite_frame=rewrite, context_frame=context)

    assert context.relation_to_previous == "ambiguous"
    assert context.missing_context
    assert route.primary_skill == "clarification"
    assert route.selected_skills == ["clarification"]
    assert "runtime_status" in route.blocked_skills
    assert all(item.startswith("clarification/") for item in route.load_set)


def test_permission_scope_question_does_not_reuse_previous_context() -> None:
    manager = _manager_with_artifacts(_artifact(asset="J1"))
    intent = IntentFrameBuilder().build("我有哪些设备权限，能基于刚才结果生成报告吗")
    context = ContextFrameAdapter(manager).resolve(
        thread_id="thread.v2.context",
        raw_message="我有哪些设备权限，能基于刚才结果生成报告吗",
        intent_frame=intent,
        auth_context=_engineer(asset_scope=["J2"]),
    )

    assert context.inherited_slots == {}
    assert context.referenced_artifact_id is None
    assert context.reuse_blockers
    assert "权限" in " ".join(context.reuse_blockers)


def test_explicit_device_switch_does_not_inherit_previous_device() -> None:
    manager = _manager_with_artifacts(_artifact(asset="J1"))
    intent = IntentFrameBuilder().build("那 J2 呢")
    context = ContextFrameAdapter(manager).resolve(
        thread_id="thread.v2.context",
        raw_message="那 J2 呢",
        intent_frame=intent,
        auth_context=_engineer(asset_scope=["J1", "J2"]),
    )
    rewrite = RewriteFrameBuilder().build("那 J2 呢", intent_frame=intent, context_frame=context)
    route = SkillRouter().route(intent_frame=intent, rewrite_frame=rewrite, context_frame=context)

    assert intent.device_refs == ["J2"]
    assert context.inherited_slots == {}
    assert context.relation_to_previous in {"new_case", "correction"}
    assert context.referenced_artifact_id is None
    assert context.permission_context["asset_scope"] == ["J1", "J2"]
    assert rewrite.user_rewrite == "查询 J2 当前运行状态"
    assert route.primary_skill == "runtime_status"
    assert route.selected_skills == ["runtime_status"]


def test_registry_discovers_skills_and_loader_only_loads_selected_skill() -> None:
    registry = SkillRegistry()
    discovered = registry.discover()
    loader = SkillLoader(registry)
    loaded = loader.load(["runtime_status"])

    assert {"runtime_status", "fault_code_explain", "clarification"}.issubset(discovered)
    assert list(loaded) == ["runtime_status"]
    assert loaded["runtime_status"].metadata.name == "runtime_status"
    assert loaded["runtime_status"].input_schema
    assert loaded["runtime_status"].examples
    assert all(item.startswith("runtime_status/") for item in loaded["runtime_status"].loaded_files)


def test_agent_engine_v2_plan_only_includes_context_and_skill_route_without_execution() -> None:
    manager = _manager_with_artifacts(_artifact())

    snapshot = AgentEngineV2().plan_only(
        raw_message="基于刚才结果生成报告",
        thread_id="thread.v2.context",
        request_id="request.phase3",
        auth_context=_engineer(),
        context_manager=manager,
    )

    assert snapshot.status == "validated"
    assert snapshot.context_frame.relation_to_previous == "report_handoff"
    assert snapshot.skill_route.primary_skill == "report_generation"
    assert snapshot.skill_route.load_set
    assert [node["node_type"] for node in snapshot.execution_plan.nodes] == ["report"]
    assert snapshot.execution_plan.allowed_tools == ["report.write_draft"]
    assert snapshot.output_frame.guardrail_result["status"] == "validated"
    assert snapshot.trace["skill_route"]["selected_skills"] == ["report_generation"]
    assert "candidate_plan" in snapshot.trace
    assert "validation" in snapshot.trace
    assert snapshot.model_dump(mode="json")
