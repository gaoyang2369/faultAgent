from __future__ import annotations

from datetime import datetime, timedelta

from fault_diagnosis.agent.contracts import (
    ArtifactLineage,
    ArtifactManifest,
    EffectiveRequestFrame,
    OutputFrame,
    PlanGoal,
)
from fault_diagnosis.agent.engine import AgentEngineV2
from fault_diagnosis.agent.output.answer import build_output_frame
from fault_diagnosis.agent.output.artifact_manifest import build_artifact_manifests
from fault_diagnosis.agent.context.artifact_access import resolve_target_artifact
from fault_diagnosis.agent.output.sse_projection import project_complete
from fault_diagnosis.agent.runtime.state import RuntimeState
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    FaultCodeEntry,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.domain.diagnosis.runtime_status import DataBasis, RuntimeStatusAssessment
from fault_diagnosis.domain.context import ArtifactBackedCaseStore, ContextManager
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.domain.diagnosis.report_mapper import _historical_data_quality
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    configure_artifact_store_backend,
    get_artifact_by_id,
    save_thread_artifact,
)


def _admin():
    return build_auth_context(user_id="admin-v2", role="admin", session_id="session-v2")


def _complete_manifest(artifact_id: str, artifact_type: str, device: str) -> dict:
    return ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id="thread.v2.contract",
        status="completed",
        followupable=True,
        reportable=True,
        actionable=artifact_type in {"analysis_artifact", "report_artifact"},
        device_refs=[device],
        owner_user_id="admin-v2",
        owner_session_id="session-v2",
        lineage=ArtifactLineage(
            lineage_status="complete",
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            subject_device_refs=[device],
            source_artifact_ids=[] if artifact_type == "sql_artifact" else ["sql:g120:2"],
            source_tables=["real_data_02"],
            time_windows=[{"start": "2026-07-12T00:00:00", "end": "2026-07-12T01:00:00"}],
            created_from_goal_ids=["goal_previous"],
        ),
    ).model_dump(mode="json")


def test_e03_preserves_four_explicit_goals_and_goal_scoped_nodes() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="A07089 是什么意思？查询 G120电机1 最近有没有相关异常，判断现在是否存在故障，并给出处理建议。",
        auth_context=_admin(),
    )

    assert snapshot.effective_request_frame.requested_goals == [
        "explain_fault_code",
        "diagnose_fault",
        "check_runtime_status",
        "resolution_recommendation",
    ]
    node_types = [node.node_type for node in snapshot.execution_plan.nodes]
    assert node_types.count("rag") == 1
    assert node_types.count("sql") == 1
    assert node_types.count("analysis") == 1
    assert snapshot.effective_request_frame.goal_query_specs[0].rag_query.startswith("A07089")
    assert snapshot.execution_plan.nodes[1].inputs["device_refs"] == ["G120电机1"]


def test_e01_continuation_uses_runtime_artifact_without_fault_code_clarification() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    manifest = _complete_manifest("sql:g120:1", "sql_artifact", "G120电机1")
    save_thread_artifact(
        DiagnosisArtifactEnvelope(
            workflow_type=DiagnosisArtifactType.STATUS_QUERY,
            thread_id="thread.v2.contract",
            created_at="2026-07-13T00:00:00",
            request_summary="查询 G120电机1 运行状态",
            final_answer="G120电机1 状态已查询",
            payload={
                "artifact_manifests": [manifest],
                "sql_artifact": {"artifact_id": "sql:g120:1", "success": True, "summary": "运行状态数据", "source_table": "real_data_01"},
            },
        )
    )
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="从刚才的结果来看，它有没有异常？详细诊断一下。",
        thread_id="thread.v2.contract",
        auth_context=_admin(),
        context_manager=ContextManager(case_store=ArtifactBackedCaseStore()),
    )
    assert snapshot.context_frame.relation_to_previous == "continuation"
    assert snapshot.effective_request_frame.effective_device_refs == ["G120电机1"]
    assert snapshot.effective_request_frame.target_artifact_id == "sql:g120:1"
    assert snapshot.effective_request_frame.effective_fault_code_refs == []
    assert snapshot.effective_request_frame.needs_clarification is False
    assert [node.node_type for node in snapshot.execution_plan.nodes] == ["analysis"]


def test_a01_compiles_one_authorized_sql_branch_per_device_and_comparison() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="比较一下 G120电机1 和 G120电机2 最近一小时的运行情况。",
        auth_context=_admin(),
    )

    assert snapshot.effective_request_frame.target_scope.operation == "compare"
    sql_nodes = [node for node in snapshot.execution_plan.nodes if node.node_type == "sql"]
    assert [node.inputs["device_refs"] for node in sql_nodes] == [["G120电机1"], ["G120电机2"]]
    assert [node.requested_tables for node in sql_nodes] == [["real_data_01"], ["real_data_02"]]
    assert [node.node_type for node in snapshot.execution_plan.nodes][-1] == "comparison"


def test_a03_replace_excludes_old_device_from_scope_plan_and_tables() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="不看G120电机1了，改查G120电机2",
        auth_context=_admin(),
    )

    scope = snapshot.effective_request_frame.target_scope
    assert scope.operation == "replace"
    assert scope.excluded_devices == ["G120电机1"]
    assert scope.resolved_devices == ["G120电机2"]
    sql = next(node for node in snapshot.execution_plan.nodes if node.node_type == "sql")
    assert sql.inputs["device_refs"] == ["G120电机2"]
    assert sql.requested_tables == ["real_data_02"]


def test_a02_diagnosis_report_uses_analysis_before_report_and_single_device_workorder() -> None:
    sql_manifest = _complete_manifest("sql:g120:2", "sql_artifact", "G120电机2")
    second = AgentEngineV2().build_plan_snapshot(
        raw_message="分析一下是否存在异常，并生成运行报告",
        thread_id="thread.v2.contract",
        auth_context=_admin(),
        conversation_context={"artifact_manifests": [sql_manifest]},
    )
    node_types = [node.node_type for node in second.execution_plan.nodes]
    assert node_types == ["analysis", "report"]
    assert second.effective_request_frame.effective_device_refs == ["G120电机2"]

    report_manifest = _complete_manifest("report:g120:2", "report_artifact", "G120电机2")
    third = AgentEngineV2().build_plan_snapshot(
        raw_message="根据报告生成工单",
        thread_id="thread.v2.contract",
        auth_context=_admin(),
        conversation_context={"artifact_manifests": [report_manifest]},
    )
    assert third.effective_request_frame.needs_clarification is False
    workorder = next(node for node in third.execution_plan.nodes if node.node_type == "workorder")
    assert workorder.inputs["device_refs"] == ["G120电机2"]
    assert workorder.inputs["draft_only"] is True
    assert workorder.inputs["manual_confirmation_required"] is True


def test_workorder_requires_exactly_one_device_for_multi_device_report() -> None:
    manifest = ArtifactManifest.model_validate(_complete_manifest("report:multi", "report_artifact", "G120电机1"))
    manifest.device_refs = ["G120电机1", "G120电机2"]
    manifest.lineage.subject_device_refs = list(manifest.device_refs)
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="根据报告生成工单",
        thread_id="thread.v2.contract",
        auth_context=_admin(),
        conversation_context={"artifact_manifests": [manifest.model_dump(mode="json")]},
    )
    assert snapshot.effective_request_frame.needs_clarification is True
    assert snapshot.effective_request_frame.ambiguity["slot"] == "exactly_one_device"


def test_artifact_lookup_is_exact_and_never_falls_back_to_latest() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    old_manifest = _complete_manifest("analysis:old", "analysis_artifact", "G120电机2")
    latest_manifest = _complete_manifest("analysis:latest", "analysis_artifact", "G120电机1")
    for index, manifest in enumerate((old_manifest, latest_manifest), start=1):
        save_thread_artifact(
            DiagnosisArtifactEnvelope(
                workflow_type=DiagnosisArtifactType.FAULT_DIAGNOSIS,
                thread_id="thread.v2.contract",
                created_at=f"2026-07-13T00:00:0{index}",
                request_summary="artifact",
                final_answer="ok",
                payload={"artifact_manifests": [manifest], "analysis_artifact": {"success": True, "conclusion": manifest["artifact_id"]}},
            )
        )
    selected = get_artifact_by_id("thread.v2.contract", "analysis:old")
    assert selected is not None
    assert selected.manifest["artifact_id"] == "analysis:old"
    assert get_artifact_by_id("thread.v2.contract", "analysis:missing") is None


def test_composite_output_keeps_all_deliverables_in_legacy_content() -> None:
    assessment = RuntimeStatusAssessment(
        device="G120电机1",
        query_status="success",
        runtime_status="attention",
        data_basis=DataBasis(
            resolution_mode="latest_available_fallback",
            latest_sample_time=datetime.now() - timedelta(days=1),
            usable_for_status=True,
            usable_for_diagnosis=True,
            usable_for_report=True,
        ),
        sample_count=10,
    )
    goals = [
        PlanGoal(goal_id="g1", requested_deliverables=["fault_code_explanation"]),
        PlanGoal(goal_id="g2", requested_deliverables=["runtime_status"]),
        PlanGoal(goal_id="g3", requested_deliverables=["diagnosis"]),
        PlanGoal(goal_id="g4", requested_deliverables=["recommendations"]),
    ]
    frame = build_output_frame(
        artifacts={
            "knowledge_artifact": KnowledgeStepArtifact(
                success=True,
                query="A07089",
                fault_codes=["A07089"],
                fault_code_entries=[FaultCodeEntry(code="A07089", meaning="速度偏差")],
            ),
            "runtime_status_assessment": assessment,
            "analysis_artifact": AnalysisStepArtifact(
                success=True,
                conclusion="存在速度偏差迹象",
                recommendations=["检查编码器"],
            ),
        },
        goals=goals,
    )
    assert [item.status for item in frame.composite_output.deliverables] == ["completed"] * 4
    assert all(title in frame.final_answer for title in ("【故障码解释】", "【运行状态】", "【综合诊断】", "【处理建议】"))
    assert frame.answer_variant == "diagnosis_answer"


def test_legacy_manifest_defaults_to_legacy_partial() -> None:
    legacy = ArtifactManifest(artifact_id="legacy", artifact_type="report_artifact")
    assert legacy.lineage.lineage_status == "legacy_partial"


def test_legacy_partial_target_is_rejected_without_latest_fallback() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    legacy = ArtifactManifest(
        artifact_id="report:legacy",
        artifact_type="report_artifact",
        thread_id="thread.v2.contract",
        device_refs=["G120电机2"],
        owner_user_id="admin-v2",
        owner_session_id="session-v2",
    ).model_dump(mode="json")
    latest = _complete_manifest("report:latest", "report_artifact", "G120电机2")
    for manifest in (legacy, latest):
        save_thread_artifact(
            DiagnosisArtifactEnvelope(
                workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
                thread_id="thread.v2.contract",
                created_at=datetime.now().isoformat(),
                request_summary="report",
                final_answer="ok",
                payload={"artifact_manifests": [manifest], "report_artifact": {"success": True}},
            )
        )
    access = resolve_target_artifact(
        thread_id="thread.v2.contract",
        artifact_id="report:legacy",
        auth=_admin(),
        expected_types={"report_artifact"},
        expected_devices=["G120电机2"],
        require_complete_lineage=True,
    )
    assert access.allowed is False
    assert access.code == "target_artifact_lineage_incomplete"


def test_report_manifest_inherits_device_table_and_source_lineage() -> None:
    assessment = RuntimeStatusAssessment(
        device="G120电机2",
        query_status="success",
        runtime_status="attention",
        data_basis=DataBasis(
            resolution_mode="realtime_window",
            latest_sample_time=datetime.now(),
            usable_for_status=True,
            usable_for_diagnosis=True,
            usable_for_report=True,
        ),
        sample_count=5,
    )
    sql = SqlStepArtifact(
        artifact_id="sql:lineage:2",
        success=True,
        summary="status",
        source_table="real_data_02",
        data_basis=assessment.data_basis.model_dump(mode="json"),
        resolved_window={"start": "2026-07-13T00:00:00", "end": "2026-07-13T01:00:00"},
    )
    analysis = AnalysisStepArtifact(
        artifact_id="analysis:lineage:2",
        success=True,
        conclusion="G120电机2 存在异常迹象",
        recommendations=["复核设备"],
    )
    report = ReportStepArtifact(
        artifact_id="report:lineage:2",
        success=True,
        report_url="/reports/g120_2.html",
    )
    manifests = build_artifact_manifests(
        thread_id="thread.v2.contract",
        artifacts={
            "sql_artifact": sql,
            "runtime_status_assessment": assessment,
            "analysis_artifact": analysis,
            "structured_analysis_artifact": {"asset": "G120电机2", "diagnosis_summary": analysis.conclusion},
            "report_artifact": report,
        },
        node_results=[
            {"node_id": "sql_1", "node_type": "sql", "goal_ids": ["g_status"]},
            {"node_id": "analysis_1", "node_type": "analysis", "goal_ids": ["g_diagnosis"]},
            {"node_id": "report_1", "node_type": "report", "goal_ids": ["g_report"]},
        ],
        auth_summary=_admin().audit_summary(),
    )
    report_manifest = next(item for item in manifests if item.artifact_type == "report_artifact")
    assert report_manifest.artifact_id == "report:lineage:2"
    assert report_manifest.lineage.lineage_status == "complete"
    assert report_manifest.lineage.subject_device_refs == ["G120电机2"]
    assert report_manifest.lineage.source_tables == ["real_data_02"]
    assert "analysis:lineage:2" in report_manifest.lineage.source_artifact_ids
    assert report_manifest.lineage.created_from_goal_ids == ["g_report"]


def test_historical_report_age_uses_real_datetime_delta() -> None:
    sample_time = datetime.now() - timedelta(days=33)
    quality = _historical_data_quality([{"create_time": sample_time.isoformat(sep=" ")}])
    age = float(quality["freshness_seconds"])
    assert 32 * 86400 < age < 34 * 86400
