from __future__ import annotations

import json

import pytest

from fault_diagnosis.agent import ExecutionPlan, WorkflowRuntimeExecutor
from fault_diagnosis.agent.output import (
    build_output_frame,
    build_reportable_payload,
    project_artifact_envelope,
)
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactType,
    DiagnosisRequest,
    EvidenceBundle,
    EvidenceItem,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.agent.output.diagnosis_payload import build_diagnosis_contract_payload


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="bundle.v2.output",
        trace_id="trace.v2.output",
        task={"asset": "J1号机"},
        evidence_items=[
            EvidenceItem(
                evidence_id="ev_sql_1",
                evidence_type="device_status",
                source_type="sql",
                source_name="real_data_01",
                summary="J1号机采样窗口内出现 A07089。",
            )
        ],
        claims=[],
        final_claim_ids=[],
        quality_checks={"missing_evidence": ["现场复核记录"]},
    )


def _analysis() -> AnalysisStepArtifact:
    return AnalysisStepArtifact(
        success=True,
        conclusion="J1号机存在 A07089 相关异常，需要复核当前告警状态。",
        basis=["SQL 采样窗口包含 A07089。"],
        recommendations=["刷新实时数据。"],
        missing_information=["现场复核记录"],
        confidence="medium",
    )


def _plan(nodes: list[dict] | None = None) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan.v2.output",
        plan_version="v2.phase8.validated",
        goals=[{"goal": "诊断 J1 A07089"}],
        nodes=nodes
        or [
            {"node_id": "sql_1", "node_type": "sql"},
            {"node_id": "analysis_1", "node_type": "analysis"},
        ],
        allowed_tools=["sql.read", "rag.search"],
        required_evidence=["latest_runtime_status"],
        expected_outputs=["diagnosis"],
    )


def test_build_output_frame_variants_are_stable() -> None:
    sql = SqlStepArtifact(success=True, summary="SQL 查询完成，解析出 1 条运行记录。", data_state="ok")
    knowledge = KnowledgeStepArtifact(
        success=True,
        query="A07089",
        snippets=["故障码：A07089\n来源：基础PDF知识库\n文档片段：A07089 表示速度偏差或负载异常。"],
        raw_output="故障码：A07089\n文档片段：A07089 表示速度偏差或负载异常。",
        fault_codes=["A07089"],
    )
    report = ReportStepArtifact(success=True, report_filename="demo.html", report_url="/reports/demo.html")

    status_frame = build_output_frame(status="completed", artifacts={"sql_artifact": sql}, evidence_bundle=_bundle())
    knowledge_frame = build_output_frame(
        status="completed",
        artifacts={"knowledge_artifact": knowledge},
        evidence_bundle=_bundle(),
    )
    diagnosis_frame = build_output_frame(
        status="completed",
        artifacts={"analysis_artifact": _analysis()},
        evidence_bundle=_bundle(),
    )
    report_frame = build_output_frame(status="completed", artifacts={"report_artifact": report}, evidence_bundle=_bundle())
    blocked_frame = build_output_frame(status="blocked", error={"message": "Approval boundary blocked execution."})
    error_frame = build_output_frame(status="failed", error={"message": "fake failure"})
    clarification_frame = build_output_frame(status="completed", requested_variant="clarification")

    assert status_frame.answer_variant == "status_brief"
    assert "SQL 查询完成" in status_frame.final_answer
    assert knowledge_frame.answer_variant == "knowledge_answer"
    assert "A07089" in knowledge_frame.final_answer
    assert "速度偏差" in knowledge_frame.final_answer
    assert diagnosis_frame.answer_variant == "diagnosis_answer"
    assert "诊断结论" in diagnosis_frame.final_answer
    assert report_frame.answer_variant == "report_ready"
    assert "/reports/demo.html" in report_frame.final_answer
    assert blocked_frame.answer_variant == "blocked"
    assert "blocked" in blocked_frame.final_answer.lower()
    assert error_frame.answer_variant == "error"
    assert "fake failure" in error_frame.final_answer
    assert clarification_frame.answer_variant == "clarification"
    assert "补充" in clarification_frame.final_answer


def test_runtime_complete_payload_contains_frontend_compat_fields() -> None:
    result = WorkflowRuntimeExecutor().execute(
        _plan(),
        trace_id="trace.v2.output",
        thread_id="thread.v2.output",
        request_id="request.v2.output",
    )

    complete = result.complete_payload
    for key in [
        "decision",
        "sql_artifact",
        "knowledge_artifact",
        "analysis_artifact",
        "report_artifact",
        "evidence_bundle",
        "artifact",
        "todos",
    ]:
        assert key in complete
    assert complete["type"] == "chat_complete"
    assert complete["runtime"] == "agent_engine_v2"
    assert complete["decision"]["primary_task_type"] == "fault_diagnosis"
    assert complete["decision"]["runtime_tools"] == ["sql.read", "rag.search"]
    assert complete["workflow_route"]["primary_task_type"] == complete["decision"]["primary_task_type"]
    assert complete["workflow_policy"]["allowed_tools"] == ["sql.read", "rag.search"]
    assert complete["workflow_result"]["status"] == "completed"
    assert complete["workflow_envelope"]["plan_id"] == "plan.v2.output"
    assert {todo["status"] for todo in complete["todos"]} <= {"pending", "running", "completed", "interrupted"}
    assert complete["evidence_bundle"]["bundle_id"]
    assert result.output_frame.final_answer == complete["rendered_answer"]["final_answer"]


def test_cancelled_complete_payload_remains_compatible() -> None:
    class CancellingNode:
        node_type = "cancel"

        def run(self, *, node, state):  # noqa: ANN001
            from fault_diagnosis.agent.runtime import NodeExecutionOutput

            state.cancel_token.cancel("user_stop")
            return NodeExecutionOutput(output={"status": "cancelled"})

    result = WorkflowRuntimeExecutor(node_registry={"cancel": CancellingNode()}).execute(
        _plan(nodes=[{"node_id": "cancel_1", "node_type": "cancel"}]),
        trace_id="trace.cancel.output",
        thread_id="thread.cancel.output",
    )

    assert result.cancel_payload is not None
    assert result.cancel_payload["type"] == "chat_complete"
    assert result.cancel_payload["cancelled"] is True
    assert result.cancel_payload["cancel_reason"] == "user_stop"
    assert result.cancel_payload["final_content"] == ""
    assert result.cancel_payload["todos"] == []


def test_v2_artifact_projection_saves_existing_envelope_contract() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    frame = build_output_frame(
        status="completed",
        artifacts={"analysis_artifact": _analysis()},
        evidence_bundle=_bundle(),
    )
    envelope = save_thread_artifact(
        project_artifact_envelope(
            thread_id="thread.artifact.output",
            output_frame=frame,
            evidence_bundle=_bundle(),
            artifacts={"analysis_artifact": _analysis()},
            node_results=[],
            trace={"trace_id": "trace.artifact.output"},
            request_summary="诊断 J1 A07089",
        )
    )
    contract = build_diagnosis_contract_payload(envelope)

    assert envelope.workflow_type == DiagnosisArtifactType.FAULT_DIAGNOSIS
    assert envelope.payload["output_frame"]["answer_variant"] == "diagnosis_answer"
    assert contract["evidence_count"] == 1
    assert contract["normalized_evidences"][0]["evidence_id"] == "ev_sql_1"
    assert "findings" in contract


def test_artifact_type_mapping_for_status_report_and_clarification() -> None:
    status = project_artifact_envelope(
        thread_id="thread.status",
        output_frame=build_output_frame(status="completed", artifacts={"sql_artifact": SqlStepArtifact(success=True, summary="ok")}),
    )
    report = project_artifact_envelope(
        thread_id="thread.report",
        output_frame=build_output_frame(
            status="completed",
            artifacts={"report_artifact": ReportStepArtifact(success=True, report_filename="demo.html")},
        ),
    )
    clarification = project_artifact_envelope(
        thread_id="thread.clarify",
        output_frame=build_output_frame(status="completed", requested_variant="clarification"),
    )

    assert status.workflow_type == DiagnosisArtifactType.STATUS_QUERY
    assert report.workflow_type == DiagnosisArtifactType.REPORT_GENERATION
    assert clarification.workflow_type == DiagnosisArtifactType.CLARIFICATION
    knowledge = project_artifact_envelope(
        thread_id="thread.knowledge",
        output_frame=build_output_frame(
            status="completed",
            artifacts={"knowledge_artifact": KnowledgeStepArtifact(success=True, query="A07089", snippets=["A07089 说明"])},
        ),
    )
    assert knowledge.workflow_type == DiagnosisArtifactType.KNOWLEDGE_QA


def test_reportable_payload_uses_structured_inputs_only() -> None:
    request = DiagnosisRequest(
        user_message="请生成 J1 A07089 诊断报告",
        user_identity="维修员",
        equipment_hint="J1号机",
        fault_code_hint="A07089",
        needs_report=True,
        report_format="html",
        analysis_goal="生成结构化报告",
    )
    row = (
        1,
        "2026-07-08 10:00:00",
        "J1号机",
        "INV-J1",
        "2026-07-08",
        "10:00:00",
        "异常",
        "A07089",
        "",
        "0",
        "0",
        560.0,
        1000.0,
        700.0,
        12.0,
        10.0,
        8.0,
        32.0,
        72.0,
        66.0,
        11.0,
        1.0,
        1.0,
        100.0,
        68.0,
        82.0,
        81.0,
        50.0,
        12.0,
        2.0,
        "2026-07-08 10:00:00",
    )
    payload = build_reportable_payload(
        request=request,
        sql_artifact=SqlStepArtifact(
            success=True,
            summary="SQL 查询完成",
            sql_used=["SELECT * FROM real_data_01"],
            raw_output=str([row]),
            data_state="ok",
        ),
        knowledge_artifact=KnowledgeStepArtifact(success=True, query="A07089", snippets=["A07089 表示运行异常"]),
        analysis_artifact=_analysis(),
        title="DCMA 故障诊断报告",
        diagnosis_type="A07089 故障诊断",
    )

    assert set(payload) >= {"title", "chart_payload", "operation_report_payload"}
    operation = json.loads(payload["operation_report_payload"])
    assert operation["title"] == "DCMA 故障诊断报告"
    assert operation["asset"]
    assert "workflow_" not in payload


def test_reportable_payload_rejects_unstructured_missing_inputs() -> None:
    with pytest.raises(TypeError):
        build_reportable_payload(
            request={"user_message": "x", "user_identity": "u", "analysis_goal": "x"},
            sql_artifact=None,
            knowledge_artifact={},
            analysis_artifact={},
        )
