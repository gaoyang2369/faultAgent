from __future__ import annotations

import pytest
from pydantic import ValidationError

from fault_diagnosis.agent import ExecutionPlan
from fault_diagnosis.agent.artifacts import ArtifactPayloadError, require_payload
from fault_diagnosis.agent.runtime.state import RuntimeState
from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    ArtifactEnvelope,
    ArtifactLineage,
    ArtifactManifest,
    ComparisonArtifactPayload,
    KnowledgeArtifactPayload,
    ReportArtifactPayload,
    SqlArtifactPayload,
    WorkorderArtifactPayload,
)
from fault_diagnosis.domain.artifacts.legacy_loader import load_artifact_envelope
from fault_diagnosis.domain.context.contracts import PendingAction
from fault_diagnosis.domain.diagnosis.analysis import diagnose_dcma_runtime
from fault_diagnosis.domain.diagnosis.analysis.contracts import DiagnosticAssessment, StructuredAnalysisArtifact
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    FaultCodeEntry,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderDraftArtifact,
)
from fault_diagnosis.domain.diagnosis.runtime_status import (
    ComparisonFinding,
    DataBasis,
    RuntimeComparisonArtifact,
    RuntimeStatusAssessment,
)
from fault_diagnosis.domain.diagnosis.reporting.payloads import build_report_payload
from fault_diagnosis.domain.security.sql_safety import select_real_data_table


def _identity() -> tuple[ArtifactManifest, ArtifactLineage]:
    lineage = ArtifactLineage(
        lineage_status="complete",
        artifact_id="art_sql_phase3",
        artifact_type="sql_artifact",
        subject_device_refs=["G120电机2"],
        source_tables=["real_data_02"],
    )
    manifest = ArtifactManifest(
        artifact_id="art_sql_phase3",
        artifact_type="sql_artifact",
        thread_id="thread_phase3",
        artifact_status="complete",
        lineage=lineage,
    )
    return manifest, lineage


def _envelope(artifact_type: str, payload) -> ArtifactEnvelope:
    artifact_id = f"art_{artifact_type}_phase3"
    lineage = ArtifactLineage(
        lineage_status="complete",
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        subject_device_refs=["G120电机2"],
    )
    manifest = ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id="thread_phase3",
        artifact_status="complete",
        lineage=lineage,
    )
    return ArtifactEnvelope(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id="thread_phase3",
        payload=payload,
        manifest=manifest,
        lineage=lineage,
    )


def _assessment() -> RuntimeStatusAssessment:
    return RuntimeStatusAssessment(
        device="G120电机2",
        query_status="success",
        runtime_status="abnormal",
        data_basis=DataBasis(
            resolution_mode="latest_available_fallback",
            usable_for_status=True,
            usable_for_diagnosis=True,
        ),
        sample_count=1,
        event_codes=["F30899"],
    )


def _payloads():
    assessment = _assessment()
    analysis_step = AnalysisStepArtifact(success=True, conclusion="F30899 持续存在")
    return [
        SqlArtifactPayload(
            sql_artifact=SqlStepArtifact(
                success=True,
                summary="typed SQL",
                source_table="real_data_02",
                query_status="success",
            ),
            runtime_status_assessment=assessment,
            normalized_rows=[{"fault_code": "F30899"}],
        ),
        KnowledgeArtifactPayload(
            knowledge_artifact=KnowledgeStepArtifact(
                success=True,
                query="F30899",
                fault_codes=["F30899"],
                fault_code_entries=[FaultCodeEntry(code="F30899", meaning="制动异常")],
            )
        ),
        AnalysisArtifactPayload(
            structured_analysis=StructuredAnalysisArtifact(
                assessment=DiagnosticAssessment(
                    success=True,
                    asset="G120电机2",
                    source_table="real_data_02",
                    conclusion="F30899 持续存在",
                ),
                analysis_artifact=analysis_step,
            )
        ),
        ComparisonArtifactPayload(
            comparison_artifact=RuntimeComparisonArtifact(
                devices=["G120电机1", "G120电机2"],
                assessments=[assessment],
                comparison_dimensions=[
                    ComparisonFinding(
                        dimension="故障码",
                        values_by_device={"G120电机2": "F30899"},
                        conclusion="电机2异常",
                    )
                ],
                conclusion="电机2风险更高",
            )
        ),
        ReportArtifactPayload(
            report_artifact=ReportStepArtifact(
                success=True,
                report_filename="phase3.html",
                report_url="/reports/phase3.html",
            )
        ),
        WorkorderArtifactPayload(
            workorder_draft=WorkOrderDraftArtifact(
                draft_id="workorder-phase3",
                source_diagnosis_artifact_id="analysis-phase3",
                device="G120电机2",
                source_hash="hash-phase3",
            ),
            pending_action=PendingAction(action_type="workorder_draft"),
        ),
    ]


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan.phase3",
        plan_version="v2.phase3.validated",
        goals=[],
        nodes=[],
    )


def _request(device: str) -> DiagnosisRequest:
    return DiagnosisRequest(
        user_message=f"检查{device}当前状态",
        user_identity="engineer",
        equipment_hint=device,
        needs_report=False,
        analysis_goal="检查运行状态",
    )


def test_canonical_envelope_rejects_untyped_dict_payload() -> None:
    manifest, lineage = _identity()
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="art_sql_phase3",
            artifact_type="sql_artifact",
            thread_id="thread_phase3",
            payload={"sql_artifact": {"success": True, "summary": "legacy dict"}},
            manifest=manifest,
            lineage=lineage,
        )


def test_runtime_state_has_one_canonical_artifact_registry() -> None:
    fields = RuntimeState.model_fields
    assert "artifact_registry" in fields
    assert "artifacts" not in fields
    assert "artifact_envelopes" not in fields


@pytest.mark.parametrize("payload", _payloads(), ids=lambda value: value.payload_type)
def test_six_typed_payloads_round_trip_through_discriminator(payload) -> None:
    envelope = _envelope(payload.payload_type, payload)
    restored = ArtifactEnvelope.model_validate_json(envelope.model_dump_json())

    assert restored.payload.payload_type == payload.payload_type
    assert type(restored.payload) is type(payload)


def test_require_payload_returns_model_and_blocks_type_mismatch() -> None:
    payload = ReportArtifactPayload(
        report_artifact=ReportStepArtifact(success=True, report_url="/reports/phase3.html")
    )
    envelope = _envelope("report_artifact", payload)

    assert require_payload(envelope, ReportArtifactPayload) is payload
    with pytest.raises(ArtifactPayloadError) as exc_info:
        require_payload(envelope, SqlArtifactPayload)
    assert exc_info.value.code == "artifact_payload_type_mismatch"


def test_require_payload_blocks_incomplete_constructed_model() -> None:
    invalid_payload = SqlArtifactPayload.model_construct(
        payload_type="sql_artifact",
        sql_artifact=SqlStepArtifact(success=True, summary="incomplete", source_table="real_data_02"),
    )
    invalid_envelope = ArtifactEnvelope.model_construct(
        artifact_id="art_sql_invalid",
        artifact_type="sql_artifact",
        thread_id="thread_phase3",
        payload=invalid_payload,
    )

    with pytest.raises(ArtifactPayloadError) as exc_info:
        require_payload(invalid_envelope, SqlArtifactPayload)
    assert exc_info.value.code == "artifact_payload_invalid"


def test_legacy_loader_strictly_upgrades_valid_v2_and_rejects_partial_payload() -> None:
    envelope = _envelope("sql_artifact", _payloads()[0])
    legacy = envelope.model_dump(mode="json")
    legacy["schema_version"] = "artifact_envelope.v2"
    legacy["payload"].pop("payload_type")
    legacy["manifest"]["evidence_refs"] = ["ev_sql_phase3"]

    upgraded = load_artifact_envelope(legacy)
    assert upgraded is not None
    assert upgraded.schema_version == "artifact_envelope.v3"
    assert isinstance(upgraded.payload, SqlArtifactPayload)

    legacy["payload"].pop("runtime_status_assessment")
    assert load_artifact_envelope(legacy) is None


def test_legacy_structured_analysis_type_is_normalized_to_analysis() -> None:
    envelope = _envelope("analysis_artifact", _payloads()[2])
    legacy = envelope.model_dump(mode="json")
    legacy["schema_version"] = "artifact_envelope.v2"
    legacy["artifact_type"] = "structured_analysis_artifact"
    legacy["manifest"]["artifact_type"] = "structured_analysis_artifact"
    legacy["lineage"]["artifact_type"] = "structured_analysis_artifact"
    legacy["manifest"]["lineage"]["artifact_type"] = "structured_analysis_artifact"
    legacy["manifest"]["evidence_refs"] = ["ev_analysis_phase3"]
    legacy["lineage"]["source_artifact_ids"] = ["art_sql_phase3"]
    legacy["manifest"]["lineage"]["source_artifact_ids"] = ["art_sql_phase3"]
    legacy["payload"].pop("payload_type")

    upgraded = load_artifact_envelope(legacy)
    assert upgraded is not None
    assert upgraded.artifact_type == "analysis_artifact"
    assert isinstance(upgraded.payload, AnalysisArtifactPayload)


def test_analysis_preserves_real_data_02_source_table() -> None:
    sql = SqlStepArtifact(
        success=True,
        summary="G120电机2运行数据",
        source_table="real_data_02",
        raw_output=str(
            [
                {
                    "device_name": "G120电机2",
                    "status": "fault",
                    "fault_code": "F30899",
                    "create_time": "2026-07-13 10:00:00",
                }
            ]
        ),
    )
    knowledge = KnowledgeStepArtifact(success=False, query="F30899", error="not_requested")
    result = diagnose_dcma_runtime(sql, knowledge, _request("G120电机2"))

    assert result.assessment.source_table == "real_data_02"
    assert all(item.source_name != "real_data_01" for item in result.evidence_items)


def test_report_payload_preserves_real_data_02_without_default_leak() -> None:
    sql = SqlStepArtifact(
        success=True,
        summary="G120电机2运行数据",
        source_table="real_data_02",
        raw_output=str(
            [
                {
                    "device_name": "G120电机2",
                    "status": "fault",
                    "fault_code": "F30899",
                    "create_time": "2026-07-13 10:00:00",
                }
            ]
        ),
    )
    payload = build_report_payload(
        request=_request("G120电机2"),
        sql_artifact=sql,
        knowledge_artifact=KnowledgeStepArtifact(success=False, query="F30899"),
        analysis_artifact=AnalysisStepArtifact(success=True, conclusion="F30899 持续存在"),
        current_time="2026-07-13 10:01:00",
        report_filename="motor2-phase3.html",
    )

    rendered = f"{payload['chart_payload']}\n{payload['operation_report_payload']}"
    assert "real_data_02" in rendered
    assert "real_data_01" not in rendered


def test_unregistered_device_has_no_silent_table_default() -> None:
    assert select_real_data_table(_request("未注册电机99")) is None
