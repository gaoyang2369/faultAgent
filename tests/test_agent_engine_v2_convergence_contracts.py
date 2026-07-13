from __future__ import annotations

from pathlib import Path

from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    configure_artifact_store_backend,
    get_artifact_by_id,
    get_canonical_artifact,
    reset_artifact_store_backend,
    save_thread_artifact,
)


def test_legacy_structured_sql_is_verified_and_upgraded_to_complete() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    try:
        artifact_id = "legacy-sql-verified"
        lineage = ArtifactLineage(
            lineage_status="complete",
            artifact_id=artifact_id,
            artifact_type="sql_artifact",
            subject_device_refs=["G120电机1"],
            source_tables=["real_data_01"],
            created_from_goal_ids=["legacy_goal"],
        )
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            artifact_type="sql_artifact",
            thread_id="thread.legacy.verified",
            device_refs=["G120电机1"],
            source_table="real_data_01",
            evidence_refs=["legacy-evidence-1"],
            lineage=lineage,
        )
        save_thread_artifact(
            DiagnosisArtifactEnvelope(
                workflow_type=DiagnosisArtifactType.STATUS_QUERY,
                thread_id="thread.legacy.verified",
                created_at="2026-07-13T00:00:00",
                request_summary="legacy sql",
                final_answer="summary text is not used as proof",
                payload={
                    "artifact_manifests": [manifest.model_dump(mode="json")],
                    "artifacts_by_id": {
                        artifact_id: {
                            "sql_artifact": {
                                "artifact_id": artifact_id,
                                "success": True,
                                "summary": "legacy structured sql",
                                "source_table": "real_data_01",
                            },
                            "runtime_status_assessment": {
                                "device": "G120电机1",
                                "query_status": "success",
                                "runtime_status": "abnormal",
                                "data_basis": {"resolution_mode": "no_data"},
                            },
                        }
                    },
                    "sql_artifact": {
                        "artifact_id": artifact_id,
                        "success": True,
                        "source_table": "real_data_01",
                        "normalized_rows": [{"device": "G120电机1", "status": "异常"}],
                    },
                },
            )
        )

        resolved = get_artifact_by_id("thread.legacy.verified", artifact_id)
        assert resolved is not None
        assert resolved.manifest["artifact_status"] == "complete"
        assert resolved.manifest["persistence_status"] == "committed"
        assert resolved.manifest["readback_verified"] is True
        assert get_canonical_artifact("thread.legacy.verified", artifact_id) is not None
    finally:
        reset_artifact_store_backend()


def test_legacy_natural_language_and_url_remain_partial_without_lineage_proof() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    try:
        artifact_id = "legacy-report-partial"
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            artifact_type="report_artifact",
            thread_id="thread.legacy.partial",
            report_url="/reports/looks-complete.html",
            diagnosis_summary="自然语言声称报告完整，但不能证明 lineage。",
        )
        save_thread_artifact(
            DiagnosisArtifactEnvelope(
                workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
                thread_id="thread.legacy.partial",
                created_at="2026-07-13T00:00:00",
                request_summary="legacy report",
                final_answer="报告已经生成。",
                payload={
                    "artifact_manifests": [manifest.model_dump(mode="json")],
                    "report_artifact": {"success": True, "report_url": "/reports/looks-complete.html"},
                },
            )
        )

        resolved = get_artifact_by_id("thread.legacy.partial", artifact_id)
        assert resolved is not None
        assert resolved.manifest["artifact_status"] == "legacy_partial"
        assert resolved.manifest["lineage"]["lineage_status"] == "legacy_partial"
        assert resolved.manifest["followupable"] is False
        assert get_canonical_artifact("thread.legacy.partial", artifact_id) is None
        assert get_artifact_by_id("thread.legacy.partial", "missing-id") is None
    finally:
        reset_artifact_store_backend()


def test_static_single_source_entrypoints() -> None:
    repo = Path(__file__).resolve().parents[1]
    artifacts_source = (repo / "fault_diagnosis/agent/artifacts.py").read_text(encoding="utf-8")
    goals_source = (repo / "fault_diagnosis/agent/context/goals.py").read_text(encoding="utf-8")
    effective_source = (repo / "fault_diagnosis/agent/context/effective_request.py").read_text(encoding="utf-8")
    answer_source = (repo / "fault_diagnosis/agent/output/answer.py").read_text(encoding="utf-8")
    presenter_source = (repo / "fault_diagnosis/agent/output/presenter.py").read_text(encoding="utf-8")

    assert artifacts_source.count("def artifact_id_factory(") == 1
    assert artifacts_source.count("artifact_id_factory(artifact_type)") == 1
    assert goals_source.count("def canonicalize_requested_goals(") == 1
    assert effective_source.count("canonicalize_requested_goals(") == 1
    assert presenter_source.count("class CompositePresenter:") == 1
    assert answer_source.count("CompositePresenter().present(") == 1
    assert "def _infer_variant(" not in answer_source
    assert "def _render_answer(" not in answer_source
    assert "def _render_composite(" not in answer_source
