from __future__ import annotations

from fault_diagnosis.agent import (
    AgentEngineV2,
    ContextFrame,
    ContextFrameAdapter,
    EffectiveRequestFrame,
    IntentFrameBuilder,
    prepare_v2_execution_plan,
    RewriteFrameBuilder,
    SkillLoader,
    SkillRegistry,
    SkillRouter,
)
from fault_diagnosis.agent.contracts import ArtifactEnvelope, ArtifactLineage, ArtifactManifest
from fault_diagnosis.domain.context import ArtifactBackedCaseStore, ContextManager
from fault_diagnosis.domain.artifacts import AnalysisArtifactPayload, KnowledgeArtifactPayload, ReportArtifactPayload
from fault_diagnosis.domain.diagnosis.analysis.contracts import DiagnosticAssessment, StructuredAnalysisArtifact
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import commit_artifact, configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    FaultCodeEntry,
    KnowledgeStepArtifact,
    ReportStepArtifact,
)
from fault_diagnosis.domain.security.permissions import build_auth_context


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
            "artifact_manifests": [
                {
                    "artifact_id": f"report:{asset}",
                    "artifact_type": "report_artifact",
                    "thread_id": thread_id,
                    "status": "completed",
                    "followupable": True,
                    "reportable": True,
                    "actionable": True,
                    "device_refs": [asset],
                    "fault_code_refs": [fault_code],
                    "evidence_bundle_id": f"eb_{asset}",
                    "linked_evidence_bundle_id": f"eb_{asset}",
                    "report_url": f"/reports/{asset}.html",
                    "report_filename": f"{asset}.html",
                    "available_followups": ["generate_report", "create_workorder_draft"],
                    "available_actions": ["create_workorder_draft"],
                    "lineage": {
                        "lineage_status": "complete",
                        "artifact_id": f"report:{asset}",
                        "artifact_type": "report_artifact",
                        "subject_device_refs": [asset],
                        "source_artifact_ids": [f"analysis:{asset}"],
                    },
                },
                {
                    "artifact_id": f"analysis:{asset}",
                    "artifact_type": "analysis_artifact",
                    "thread_id": thread_id,
                    "status": "completed",
                    "followupable": True,
                    "reportable": True,
                    "actionable": True,
                    "device_refs": [asset],
                    "fault_code_refs": [fault_code],
                    "available_followups": ["generate_report", "create_workorder_draft"],
                    "available_actions": ["create_workorder_draft"],
                }
            ],
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


def _manifest_artifact(
    *,
    thread_id: str = "thread.v2.manifest",
    manifests: list[dict],
    request_summary: str = "manifest-backed artifact",
    final_answer: str = "ok",
) -> DiagnosisArtifactEnvelope:
    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.FAULT_DIAGNOSIS,
        thread_id=thread_id,
        created_at=f"2026-06-24T10:00:0{len(manifests)}",
        request_summary=request_summary,
        final_answer=final_answer,
        payload={
            "artifact_manifests": manifests,
            "latest_focus": manifests[0] if manifests else {},
        },
        evidence=[],
    )


def _manager_with_artifacts(*artifacts: DiagnosisArtifactEnvelope) -> ContextManager:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    for artifact in artifacts:
        payload = dict(artifact.payload)
        committed_manifests = []
        for raw in payload.get("artifact_manifests", []) or []:
            manifest = ArtifactManifest.model_validate(raw)
            lineage = ArtifactLineage(
                lineage_status="complete",
                artifact_id=manifest.artifact_id,
                artifact_type=manifest.artifact_type,
                subject_device_refs=list(manifest.device_refs),
                fault_code_refs=list(manifest.fault_code_refs),
                source_artifact_ids=list(manifest.lineage.source_artifact_ids),
            )
            manifest = manifest.model_copy(
                update={"artifact_status": "complete", "lineage": lineage}, deep=True
            )
            committed = commit_artifact(
                ArtifactEnvelope(
                    artifact_id=manifest.artifact_id,
                    artifact_type=manifest.artifact_type,
                    thread_id=artifact.thread_id,
                    payload=_typed_payload(manifest),
                    manifest=manifest,
                    lineage=lineage,
                )
            )
            committed_manifests.append(committed.manifest.model_dump(mode="json"))
        if committed_manifests:
            payload["artifact_manifests"] = committed_manifests
            payload["latest_focus"] = committed_manifests[0]
            artifact = artifact.model_copy(update={"payload": payload}, deep=True)
        save_thread_artifact(artifact)
    return ContextManager(case_store=ArtifactBackedCaseStore())


def _typed_payload(manifest: ArtifactManifest):
    artifact_id = manifest.artifact_id
    if manifest.artifact_type == "report_artifact":
        return ReportArtifactPayload(
            report_artifact=ReportStepArtifact(
                artifact_id=artifact_id,
                success=True,
                report_filename=manifest.report_filename or "fixture.html",
                report_url=manifest.report_url or "/reports/fixture.html",
            )
        )
    if manifest.artifact_type == "knowledge_artifact":
        code = (manifest.fault_code_refs or ["A07089"])[0]
        return KnowledgeArtifactPayload(
            knowledge_artifact=KnowledgeStepArtifact(
                artifact_id=artifact_id,
                success=True,
                query=code,
                fault_codes=list(manifest.fault_code_refs),
                fault_code_entries=[FaultCodeEntry(code=code, meaning=manifest.diagnosis_summary)],
            )
        )
    return AnalysisArtifactPayload(
        structured_analysis=StructuredAnalysisArtifact(
            assessment=DiagnosticAssessment(
                success=True,
                asset=(manifest.device_refs or ["fixture"])[0],
                source_table=manifest.source_table or "real_data_01",
                conclusion=manifest.diagnosis_summary or "fixture analysis",
            ),
            analysis_artifact=AnalysisStepArtifact(
                artifact_id=artifact_id,
                success=True,
                conclusion=manifest.diagnosis_summary or "fixture analysis",
            ),
        )
    )


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


def test_agent_engine_v2_build_plan_snapshot_includes_context_and_skill_route_without_execution() -> None:
    manager = _manager_with_artifacts(_artifact())

    snapshot = AgentEngineV2().build_plan_snapshot(
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


def test_followup_detail_inherits_latest_knowledge_artifact_fault_code() -> None:
    thread_id = "thread.v2.detail.followup"
    manager = _manager_with_artifacts(
        _manifest_artifact(
            thread_id=thread_id,
            request_summary="A07089 是什么意思",
            final_answer="A07089：单位转换后不能激活功能块。",
            manifests=[
                {
                    "schema_version": "artifact_manifest.v1",
                    "artifact_id": "knowledge:trace.detail:A07089",
                    "artifact_type": "knowledge_artifact",
                    "thread_id": thread_id,
                    "status": "completed",
                    "followupable": True,
                    "fault_code_refs": ["A07089"],
                    "diagnosis_summary": "A07089 单位转换后不能激活功能块",
                    "source_file": "S120_故障手册.pdf",
                    "source_page": "232",
                    "available_followups": ["expand_previous_answer", "show_manual_fields"],
                }
            ],
        )
    )

    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="详细点",
        thread_id=thread_id,
        request_id="request.detail.followup",
        auth_context=_engineer(),
        context_manager=manager,
    )

    assert snapshot.effective_request_frame.semantic_intent == "expand_previous_answer"
    assert snapshot.effective_request_frame.effective_fault_code_refs == ["A07089"]
    assert snapshot.effective_request_frame.requested_output_mode == "detailed"
    assert snapshot.effective_request_frame.needs_clarification is False
    assert snapshot.context_frame.relation_to_previous != "ambiguous"
    assert snapshot.context_frame.missing_context == []
    assert snapshot.context_frame.reuse_blockers == []
    assert snapshot.skill_route.primary_skill == "fault_code_explain"
    assert [node.node_type for node in snapshot.execution_plan.nodes] == ["rag"]
    assert "clarification" not in snapshot.skill_route.selected_skills

    plan = prepare_v2_execution_plan(snapshot=snapshot, thread_id=thread_id, auth_context=_engineer())
    rag_inputs = next(node.inputs for node in plan.nodes if node.node_type == "rag")
    assert "A07089" in rag_inputs["query"]
    assert "详细说明" in rag_inputs["query"]
    assert rag_inputs["query"] != "详细点"
    assert rag_inputs["retrieval_strategy"] == "fault_code_exact_then_semantic"
    assert rag_inputs["top_k"] >= 3
    assert rag_inputs["source_artifact_refs"][0]["artifact_id"] == "knowledge:trace.detail:A07089"


def test_effective_request_cannot_override_ambiguous_context_even_with_target() -> None:
    intent = IntentFrameBuilder().build("详细点")
    context = ContextFrame(
        relation_to_previous="ambiguous",
        missing_context=["请确认“刚才那个”指的是哪个故障码。"],
        reuse_blockers=["存在多个历史候选。"],
    )
    rewrite = RewriteFrameBuilder().build("详细点", intent_frame=intent, context_frame=context)
    effective = EffectiveRequestFrame(
        raw_message="详细点",
        normalized_message="详细点",
        semantic_intent="expand_previous_answer",
        task_family="knowledge",
        requested_output_mode="detailed",
        effective_fault_code_refs=["A07089"],
        target_artifact_id="knowledge:trace.detail:A07089",
        target_artifact_type="knowledge_artifact",
        needs_clarification=False,
    )

    route = SkillRouter().route(
        intent_frame=intent,
        rewrite_frame=rewrite,
        context_frame=context,
        effective_request_frame=effective,
    )

    assert route.primary_skill == "clarification"
    assert route.selected_skills == ["clarification"]
    assert route.blocked_skills


def test_followup_detail_with_multiple_fault_codes_requires_clarification() -> None:
    thread_id = "thread.v2.detail.multiple"
    manager = _manager_with_artifacts(
        _manifest_artifact(
            thread_id=thread_id,
            request_summary="A07089 和 F01002 是什么意思",
            final_answer="A07089 与 F01002 均有说明。",
            manifests=[
                {
                    "schema_version": "artifact_manifest.v1",
                    "artifact_id": "knowledge:trace.multiple",
                    "artifact_type": "knowledge_artifact",
                    "thread_id": thread_id,
                    "status": "completed",
                    "followupable": True,
                    "fault_code_refs": ["A07089", "F01002"],
                    "available_followups": ["expand_previous_answer"],
                }
            ],
        )
    )

    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="详细点",
        thread_id=thread_id,
        auth_context=_engineer(),
        context_manager=manager,
    )

    assert snapshot.context_frame.relation_to_previous == "ambiguous"
    assert snapshot.context_frame.missing_context
    assert snapshot.effective_request_frame.effective_fault_code_refs == []
    assert snapshot.skill_route.primary_skill == "clarification"


def test_followup_workorder_uses_latest_report_manifest_without_ambiguity() -> None:
    thread_id = "thread.v2.report.workorder.followup"
    manager = _manager_with_artifacts(
        _manifest_artifact(
            thread_id=thread_id,
            request_summary="生成G120电机1的运行报告",
            final_answer="报告已生成，G120电机1 存在 A07089 和速度偏差。",
            manifests=[
                {
                    "schema_version": "artifact_manifest.v1",
                    "artifact_id": "/reports/g120_motor1.html",
                    "artifact_type": "report_artifact",
                    "thread_id": thread_id,
                    "status": "completed",
                    "followupable": True,
                    "reportable": True,
                    "actionable": True,
                    "device_refs": ["G120电机1"],
                    "fault_code_refs": ["A07089"],
                    "freshness": "recent",
                    "severity": "medium",
                    "diagnosis_summary": "G120电机1 存在 A07089、速度偏差、负载率偏高和数据滞后。",
                    "report_url": "/reports/g120_motor1.html",
                    "linked_analysis_artifact_id": "analysis:g120",
                    "linked_evidence_bundle_id": "eb:g120",
                    "available_actions": ["decide_workorder", "create_workorder_draft"],
                },
                {
                    "schema_version": "artifact_manifest.v1",
                    "artifact_id": "analysis:g120",
                    "artifact_type": "analysis_artifact",
                    "thread_id": thread_id,
                    "status": "completed",
                    "followupable": True,
                    "reportable": True,
                    "actionable": True,
                    "device_refs": ["G120电机1"],
                    "fault_code_refs": ["A07089"],
                    "diagnosis_summary": "G120电机1 存在 A07089 和运行异常。",
                    "available_actions": ["decide_workorder", "create_workorder_draft"],
                },
            ],
        )
    )

    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="看起来有故障，那就创建工单",
        thread_id=thread_id,
        request_id="request.report.workorder.followup",
        auth_context=_engineer(asset_scope=["G120电机1"]),
        context_manager=manager,
    )

    assert snapshot.context_frame.relation_to_previous != "ambiguous"
    assert snapshot.effective_request_frame.effective_device_refs == ["G120电机1"]
    assert snapshot.effective_request_frame.effective_fault_code_refs == ["A07089"]
    assert snapshot.effective_request_frame.target_artifact_id == "/reports/g120_motor1.html"
    assert snapshot.effective_request_frame.target_artifact_type == "report_artifact"
    assert snapshot.effective_request_frame.needs_clarification is False
    assert snapshot.skill_route.primary_skill == "workorder_decision"
    assert [node.node_type for node in snapshot.execution_plan.nodes] == ["workorder", "approval"]

    plan = prepare_v2_execution_plan(snapshot=snapshot, thread_id=thread_id, auth_context=_engineer(asset_scope=["G120电机1"]))
    workorder_inputs = next(node.inputs for node in plan.nodes if node.node_type == "workorder")
    forbidden = {
        "previous_sql_artifact",
        "previous_knowledge_artifact",
        "previous_analysis_artifact",
        "previous_report_artifact",
        "raw_output",
        "result_preview",
    }
    assert not forbidden.intersection(workorder_inputs)
    assert workorder_inputs["source_artifact_refs"][0]["artifact_id"] == "/reports/g120_motor1.html"
    assert workorder_inputs["manual_confirmation_required"] is True
    assert workorder_inputs["draft_only"] is True
