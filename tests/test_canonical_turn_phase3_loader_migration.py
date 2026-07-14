from __future__ import annotations

import pytest

from fault_diagnosis.agent import ExecutionPlan
from fault_diagnosis.agent.artifact_migration import migrate_analysis_report_snapshot
from fault_diagnosis.agent.artifacts import (
    ArtifactPayloadError,
    load_artifact_lineage_for_audit,
    load_exact_artifact,
)
from fault_diagnosis.agent.runtime.state import RuntimeState
from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    ArtifactEnvelope,
    ArtifactLineage,
    ArtifactManifest,
    SqlArtifactPayload,
)
from fault_diagnosis.domain.diagnosis.analysis.contracts import DiagnosticAssessment, StructuredAnalysisArtifact
from fault_diagnosis.domain.diagnosis.contracts import AnalysisStepArtifact, SqlStepArtifact
from fault_diagnosis.domain.diagnosis.runtime_status import DataBasis, RuntimeStatusAssessment
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    commit_artifact,
    configure_artifact_store_backend,
    get_canonical_artifact,
)


THREAD = "thread.phase3.loader"


@pytest.fixture(autouse=True)
def _memory_store():
    configure_artifact_store_backend(MemoryArtifactStoreBackend())


def _admin():
    return build_auth_context(user_id="owner-phase3", role="admin")


def _engineer(user_id: str = "owner-phase3"):
    return build_auth_context(
        user_id=user_id,
        role="engineer",
        asset_scope=["G120电机1"],
        table_scope=["real_data_01"],
    )


def _sql(artifact_id: str = "sql:phase3") -> ArtifactEnvelope:
    assessment = RuntimeStatusAssessment(
        device="G120电机1",
        query_status="success",
        runtime_status="abnormal",
        data_basis=DataBasis(
            freshness="recent",
            usable_for_status=True,
            usable_for_diagnosis=True,
            usable_for_report=True,
            usable_for_workorder_draft=True,
        ),
        sample_count=2,
        event_codes=["A07089"],
    )
    return _commit(
        artifact_id=artifact_id,
        artifact_type="sql_artifact",
        payload=SqlArtifactPayload(
            sql_artifact=SqlStepArtifact(success=True, summary="rows", source_table="real_data_01", query_status="success"),
            runtime_status_assessment=assessment,
            normalized_rows=[{"device_name": "G120电机1", "fault_code": "A07089"}],
        ),
        sources=[],
    )


def _analysis(
    artifact_id: str = "analysis:legacy",
    *,
    sources: list[str] | None = None,
    linked_sql: str = "",
) -> ArtifactEnvelope:
    structured = StructuredAnalysisArtifact(
        assessment=DiagnosticAssessment(
            success=True,
            asset="G120电机1",
            source_table="real_data_01",
            sample_count=2,
            event_codes=["A07089"],
            conclusion="A07089 持续出现",
            recommendations=["检查速度反馈"],
        ),
        analysis_artifact=AnalysisStepArtifact(
            success=True,
            conclusion="A07089 持续出现",
            recommendations=["检查速度反馈"],
        ),
    )
    return _commit(
        artifact_id=artifact_id,
        artifact_type="analysis_artifact",
        payload=AnalysisArtifactPayload(structured_analysis=structured),
        sources=list(sources or ["sql:phase3"]),
        linked_sql=linked_sql,
    )


def _commit(*, artifact_id: str, artifact_type: str, payload, sources: list[str], linked_sql: str = ""):
    lineage = ArtifactLineage(
        lineage_status="complete",
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        subject_device_refs=["G120电机1"],
        source_artifact_ids=sources,
        direct_source_artifact_ids=sources,
        source_tables=["real_data_01"],
    )
    manifest = ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id=THREAD,
        status="completed",
        artifact_status="complete",
        owner_user_id="owner-phase3",
        device_refs=["G120电机1"],
        source_table="real_data_01",
        linked_sql_artifact_id=linked_sql,
        evidence_refs=["ev:phase3"],
        lineage=lineage,
    )
    return commit_artifact(
        ArtifactEnvelope(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            owner_user_id="owner-phase3",
            thread_id=THREAD,
            payload=payload,
            manifest=manifest,
            lineage=lineage,
        )
    )


def _state(auth=None) -> RuntimeState:
    return RuntimeState(
        plan=ExecutionPlan(plan_id="phase3-loader", plan_version="v2.canonical-phase3.validated"),
        thread_id=THREAD,
        auth_context=auth or _engineer(),
    )


def test_exact_loader_has_no_latest_or_lineage_fallback_and_enforces_type_and_auth() -> None:
    sql = _sql()
    _sql("sql:other")
    state = _state()
    assert load_exact_artifact(state=state, artifact_id=sql.artifact_id, expected_type="sql_artifact").artifact_id == sql.artifact_id
    with pytest.raises(ArtifactPayloadError) as wrong_type:
        load_exact_artifact(state=_state(), artifact_id=sql.artifact_id, expected_type="analysis_artifact")
    assert wrong_type.value.code == "target_artifact_type_mismatch"
    with pytest.raises(ArtifactPayloadError) as missing:
        load_exact_artifact(state=_state(), artifact_id="sql:missing", expected_type="sql_artifact")
    assert missing.value.code == "target_artifact_not_found"
    with pytest.raises(ArtifactPayloadError) as unauthorized:
        load_exact_artifact(state=_state(_engineer("other-user")), artifact_id=sql.artifact_id, expected_type="sql_artifact")
    assert unauthorized.value.code == "target_artifact_owner_mismatch"


def test_audit_lineage_loader_does_not_promote_ancestors_into_node_inputs() -> None:
    _sql()
    analysis = _analysis()
    state = _state()
    node = {"inputs": {"artifact_role_bindings": []}}
    before = node["inputs"].copy()
    lineage = load_artifact_lineage_for_audit(state=state, artifact_id=analysis.artifact_id)
    assert [item.artifact_id for item in lineage] == ["analysis:legacy", "sql:phase3"]
    assert node["inputs"] == before


def test_migration_dry_run_apply_immutability_and_explicit_source_checks() -> None:
    _sql()
    old = _analysis()
    timestamp = "2026-07-14T10:00:00+00:00"
    dry_run = migrate_analysis_report_snapshot(
        thread_id=THREAD,
        analysis_artifact_id=old.artifact_id,
        sql_artifact_id="sql:phase3",
        auth_context=_admin(),
        apply=False,
        migration_timestamp=timestamp,
    )
    assert dry_run.status == "dry_run" and dry_run.write_performed is False
    assert get_canonical_artifact(THREAD, dry_run.migrated_artifact_id) is None
    assert old.payload.report_input_snapshot is None

    applied = migrate_analysis_report_snapshot(
        thread_id=THREAD,
        analysis_artifact_id=old.artifact_id,
        sql_artifact_id="sql:phase3",
        auth_context=_admin(),
        apply=True,
        migration_timestamp=timestamp,
    )
    stored = get_canonical_artifact(THREAD, applied.migrated_artifact_id)
    assert stored is not None and stored.payload.report_input_snapshot is not None
    assert stored.manifest.migrated_from_artifact_id == old.artifact_id
    assert stored.manifest.migration_tool_version
    assert get_canonical_artifact(THREAD, old.artifact_id).payload.report_input_snapshot is None

    _sql("sql:wrong")
    with pytest.raises(ValueError, match="explicit_sql_mismatch"):
        migrate_analysis_report_snapshot(
            thread_id=THREAD,
            analysis_artifact_id=old.artifact_id,
            sql_artifact_id="sql:wrong",
            auth_context=_admin(),
            apply=False,
        )
    ambiguous = _analysis("analysis:ambiguous", sources=["sql:phase3", "sql:wrong"])
    with pytest.raises(ValueError, match="source_ambiguous"):
        migrate_analysis_report_snapshot(
            thread_id=THREAD,
            analysis_artifact_id=ambiguous.artifact_id,
            sql_artifact_id="sql:phase3",
            auth_context=_admin(),
            apply=False,
        )
    with pytest.raises(PermissionError, match="owner_mismatch"):
        migrate_analysis_report_snapshot(
            thread_id=THREAD,
            analysis_artifact_id=old.artifact_id,
            sql_artifact_id="sql:phase3",
            auth_context=_engineer("other-user"),
            apply=False,
        )
