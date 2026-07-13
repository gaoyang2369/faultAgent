from __future__ import annotations

from fault_diagnosis.agent.context.source_selector import GoalScopedSourceSelector, allowed_source_types
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest, EffectiveGoal
from fault_diagnosis.agent.runtime.plan_preparer import _reportable_payload
from fault_diagnosis.domain.context.conversation_context import _latest_artifact_manifests
from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    configure_artifact_store_backend,
    save_thread_artifact,
)


def _manifest(
    artifact_id: str,
    artifact_type: str,
    *,
    sources: list[str] | None = None,
    device: str = "G120电机2",
) -> ArtifactManifest:
    lineage = ArtifactLineage(
        lineage_status="complete",
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        subject_device_refs=[device],
        source_artifact_ids=list(sources or []),
    )
    return ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id="thread.phase2.selector",
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        device_refs=[device],
        lineage=lineage,
    )


def _goal(capability: str, *, policy: str = "reuse_verified_artifact") -> EffectiveGoal:
    return EffectiveGoal(goal_id=f"goal_{capability}", capability=capability, source_policy=policy)


def test_goal_source_type_matrix_is_explicit() -> None:
    assert allowed_source_types("explain_fault_code") == ("knowledge_artifact",)
    assert allowed_source_types("check_runtime_status") == ("sql_artifact",)
    assert allowed_source_types("diagnose_fault") == ("analysis_artifact", "sql_artifact")
    assert allowed_source_types("resolution_recommendation") == ("analysis_artifact", "sql_artifact")
    assert allowed_source_types("generate_report") == (
        "analysis_artifact",
        "comparison_artifact",
        "sql_artifact",
    )
    assert allowed_source_types("create_workorder_draft") == ("report_artifact", "analysis_artifact")
    assert allowed_source_types("confirm_workorder_draft") == ("workorder_artifact",)


def test_incompatible_explicit_reference_uses_only_unique_lineage_ancestor() -> None:
    report = _manifest("report-1", "report_artifact", sources=["analysis-1"])
    workorder = _manifest("workorder-1", "workorder_artifact", sources=[report.artifact_id])
    analysis = _manifest("analysis-1", "analysis_artifact", sources=["sql-1"])
    sql = _manifest("sql-1", "sql_artifact")
    selection = GoalScopedSourceSelector().select(
        goal=_goal("create_workorder_draft"),
        manifests=[workorder, report, analysis, sql],
        explicit_artifact_id=workorder.artifact_id,
        expected_devices=["G120电机2"],
        thread_id=workorder.thread_id,
    )
    assert selection.status == "selected"
    assert selection.binding is not None
    assert selection.binding.artifact_id == report.artifact_id
    assert selection.binding.selection_reason == "unique_compatible_lineage_ancestor"
    assert selection.binding.reusable_result_artifact_id == workorder.artifact_id


def test_multiple_compatible_lineage_ancestors_are_ambiguous() -> None:
    report = _manifest("report-1", "report_artifact")
    analysis = _manifest("analysis-1", "analysis_artifact")
    workorder = _manifest("workorder-1", "workorder_artifact", sources=[report.artifact_id, analysis.artifact_id])
    selection = GoalScopedSourceSelector().select(
        goal=_goal("create_workorder_draft"),
        manifests=[workorder, report, analysis],
        explicit_artifact_id=workorder.artifact_id,
        expected_devices=["G120电机2"],
        thread_id=workorder.thread_id,
    )
    assert selection.status == "ambiguous"
    assert selection.binding is None
    assert selection.observation["selection_reason"] == "multiple_compatible_lineage_ancestors"


def test_collect_new_policy_never_binds_historical_candidate() -> None:
    sql = _manifest("sql-1", "sql_artifact")
    selection = GoalScopedSourceSelector().select(
        goal=_goal("check_runtime_status", policy="refresh_runtime_data"),
        manifests=[sql],
        explicit_artifact_id=sql.artifact_id,
        expected_devices=["G120电机2"],
        thread_id=sql.thread_id,
    )
    assert selection.status == "collect_new"
    assert selection.binding is None


def test_context_manifest_inventory_recursively_loads_exact_lineage() -> None:
    chain = {
        item.artifact_id: item.model_dump(mode="json")
        for item in (
            _manifest("workorder-1", "workorder_artifact", sources=["report-1"]),
            _manifest("report-1", "report_artifact", sources=["analysis-1"]),
            _manifest("analysis-1", "analysis_artifact", sources=["sql-1"]),
            _manifest("sql-1", "sql_artifact"),
        )
    }
    manifests = _latest_artifact_manifests(
        "thread.phase2.selector",
        artifact_getter=lambda _: None,
        manifest_getter=lambda _, artifact_id: chain.get(artifact_id),
        seed_artifact_ids=["workorder-1"],
    )
    assert [item["artifact_id"] for item in manifests] == [
        "workorder-1",
        "report-1",
        "analysis-1",
        "sql-1",
    ]


def test_report_preparation_has_no_latest_artifact_fallback() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    save_thread_artifact(
        DiagnosisArtifactEnvelope(
            workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
            thread_id="thread.phase2.no-latest",
            created_at="2026-07-13T20:00:00",
            request_summary="legacy latest reportable payload",
            final_answer="ok",
            payload={"operation_report_payload": {"asset": "G120电机2"}},
        )
    )
    assert _reportable_payload("thread.phase2.no-latest", target_id="") == {}
