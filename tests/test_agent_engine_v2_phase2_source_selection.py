from __future__ import annotations

import importlib.util
import inspect

from fault_diagnosis.agent import AgentEngineV2
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.agent.runtime import plan_preparer
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


def test_retired_source_selector_and_effective_request_builder_are_absent() -> None:
    assert importlib.util.find_spec("fault_diagnosis.agent.context.source_selector") is None
    assert importlib.util.find_spec("fault_diagnosis.agent.context.effective_request") is None
    assert importlib.util.find_spec("fault_diagnosis.agent.context.goals") is None


def test_engine_facade_no_longer_accepts_legacy_planning_authorities() -> None:
    parameters = inspect.signature(AgentEngineV2.build_plan_snapshot).parameters
    assert "legacy_plan" not in parameters
    assert "llm_candidate_plan" not in parameters
    assert "recent_context_signals" not in parameters


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
    assert not hasattr(plan_preparer, "_reportable_payload")
