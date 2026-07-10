from __future__ import annotations

import json

import pytest

from fault_diagnosis.agent import ExecutionPlan, WorkflowRuntimeExecutor
from fault_diagnosis.agent.runtime import NodeExecutionOutput
from fault_diagnosis.agent.output import (
    build_output_frame,
    build_reportable_payload,
    project_artifact_envelope,
)
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactType,
    DiagnosisRequest,
    EvidenceBundle,
    EvidenceItem,
    FaultCodeEntry,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderDraftArtifact,
    WorkOrderSuggestion,
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
        forbidden_tools=["workorder.dispatch", "device_control.write", "alarm.close"],
        required_evidence=["latest_runtime_status"],
        expected_outputs=["diagnosis"],
    )


def _fault_code_entry(*, match_type: str = "exact_match", cause: str = "尝试激活功能块。转换单位后不允许此操作。", remedy: str = "将单位恢复到出厂设置。") -> FaultCodeEntry:
    return FaultCodeEntry(
        code="A07089",
        title="单位转换： 转换单位后不能激活功能块",
        meaning="转换单位后不能激活功能块",
        cause=cause,
        remedy=remedy,
        category="参数设置 / 配置 / 调试过程出错 (18)",
        drive_object="所有目标",
        component="无",
        propagation="LOCAL",
        reaction="无",
        acknowledgement="无",
        references=["p0100 ( 标准 IEC/NEMA)", "p0349 ( 电机等效电路图数据单位制 )", "p0505 ( 单位制选择 )"],
        source_file="S120_故障手册.pdf",
        page="232",
        match_type=match_type,
    )


def _workorder_suggestion() -> WorkOrderSuggestion:
    return WorkOrderSuggestion(
        lifecycle_status="recommended_draft",
        need_workorder=True,
        reason="A07089 持续出现；速度偏差 46.3% 超过关注阈值；负载率 78.47% 进入关注区间",
        workorder_type="参数复核 / 运行异常排查",
        priority="P2",
        priority_label="中优先级",
        risk_level="中",
        assignee_role="电气维护人员",
        suggested_completion_window="24 小时内完成复核",
        diagnosis_conclusion="A07089 事件持续出现，伴随速度偏差 46.3% 和负载率最高 78.47%。",
        key_evidence=["A07089 持续出现", "速度偏差 46.3%", "负载率最高 78.47%"],
        processing_steps=["刷新当前状态", "核对速度反馈链路"],
        acceptance_criteria=["当前状态已刷新", "工程师已确认派发条件"],
        equipment_object="G120电机1",
        fault_code="A07089",
        title="G120电机1 A07089 运行异常排查",
        source_diagnosis_artifact_id="ledger_trace_report",
        source_report_artifact_id="/reports/g120.html",
    )


def _pending_action() -> dict:
    return {
        "action_type": "workorder_draft",
        "status": "pending",
        "artifact_id": "workorder_recommendation:trace_report",
        "reason": "A07089 持续出现，建议生成草稿后由工程师确认。",
        "required_evidence": ["diagnosis_summary", "severity_or_status_level", "recommended_action_policy"],
        "source_diagnosis_artifact_id": "ledger_trace_report",
        "source_report_artifact_id": "/reports/g120.html",
        "recommendation_artifact_id": "workorder_recommendation:trace_report",
        "required_role": "engineer",
        "stale_refresh_required": True,
    }


def _workorder_draft() -> WorkOrderDraftArtifact:
    return WorkOrderDraftArtifact(
        draft_id="WOD-TRACEG120",
        source_diagnosis_artifact_id="ledger_trace_report",
        source_report_artifact_id="/reports/g120.html",
        device="G120电机1",
        fault_code="A07089",
        priority="P2",
        workorder_type="参数复核 / 运行异常排查",
        recommended_assignee_role="电气维护人员",
        acceptance_criteria=["当前状态已刷新", "工程师已确认派发条件"],
        stale_warning="上一轮数据已滞后约 29.5 天，正式提交或派发前必须刷新当前状态并经人工审批。",
        status="pending_verification",
        source_hash="trace-g120-a07089",
        title="G120电机1 A07089 运行异常排查",
        created_from_recommendation_artifact_id="workorder_recommendation:trace_report",
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


def test_workorder_output_frame_variant() -> None:
    artifacts = {
        "workorder_suggestion": _workorder_suggestion(),
        "workorder_pending_action": _pending_action(),
        "workorder_draft": _workorder_draft(),
    }
    frame = build_output_frame(
        status="completed",
        artifacts=artifacts,
        evidence_bundle=_bundle(),
        node_results=[
            {
                "node_id": "workorder_1",
                "node_type": "workorder",
                "output": {
                    "success": True,
                    "target_evidence_bundle_id": "ledger_trace_report",
                    "stale_evidence_disclosure_required": True,
                    "source_artifact_refs": [{"artifact_id": "/reports/g120.html", "artifact_type": "report_artifact"}],
                },
            }
        ],
    )

    assert frame.answer_variant == "workorder_draft_ready"
    assert frame.answer_variant != "clarification"
    assert "工单草稿" in frame.final_answer
    assert "未派发" in frame.final_answer
    assert "人工确认" in frame.final_answer
    assert "G120电机1" in frame.final_answer
    assert "A07089" in frame.final_answer
    assert frame.workorder_draft_payload["draft_only"] is True
    assert frame.workorder_draft_payload["manual_confirmation_required"] is True
    assert frame.workorder_draft_payload["dispatch_forbidden"] is True
    assert frame.workorder_draft_payload["target_evidence_bundle_id"] == "ledger_trace_report"
    assert frame.guardrail_result["generated_from_previous_artifact"] is True


def test_report_to_workorder_final_answer() -> None:
    class ReadyWorkorderNode:
        node_type = "workorder"

        def run(self, *, node, state):  # noqa: ANN001
            suggestion = _workorder_suggestion()
            pending = _pending_action()
            draft = _workorder_draft()
            return NodeExecutionOutput(
                output={
                    "success": True,
                    "suggestion": suggestion.model_dump(mode="json"),
                    "pending_action": pending,
                    "draft": draft.model_dump(mode="json"),
                    "target_evidence_bundle_id": "ledger_trace_report",
                    "stale_evidence_disclosure_required": True,
                    "source_artifact_refs": [{"artifact_id": "/reports/g120.html", "artifact_type": "report_artifact"}],
                },
                artifacts={
                    "workorder_suggestion": suggestion,
                    "workorder_pending_action": pending,
                    "workorder_draft": draft,
                },
            )

    plan = ExecutionPlan(
        plan_id="plan.report.to.workorder",
        plan_version="v2.phase8.validated",
        goals=[{"goal": "基于上一轮报告生成工单草稿"}],
        nodes=[
            {
                "node_id": "workorder_1",
                "node_type": "workorder",
                "inputs": {
                    "device_refs": ["G120电机1"],
                    "fault_code_refs": ["A07089"],
                    "target_artifact_id": "/reports/g120.html",
                    "target_artifact_type": "report_artifact",
                    "target_evidence_bundle_id": "ledger_trace_report",
                    "stale_evidence_disclosure_required": True,
                    "source_artifact_refs": [{"artifact_id": "/reports/g120.html", "artifact_type": "report_artifact"}],
                },
            }
        ],
        allowed_tools=["workorder.create"],
        forbidden_tools=["workorder.dispatch", "device_control.write", "alarm.close"],
        approval_requirements=[
            {
                "requirement_id": "approval_workorder_draft",
                "type": "workorder_draft",
                "required": True,
                "allowed_next_step": "draft_only",
            }
        ],
        expected_outputs=["workorder_draft"],
    )

    result = WorkflowRuntimeExecutor(node_registry={"workorder": ReadyWorkorderNode()}, real_tools=True).execute(
        plan,
        trace_id="trace.report.to.workorder",
        thread_id="thread.report.to.workorder",
        auth_context=build_auth_context(role="engineer", asset_scope=["G120电机1"], table_scope=["real_data_01"]),
    )

    assert result.status == "completed"
    assert result.node_results[0].node_id == "workorder_1"
    assert result.node_results[0].status == "completed"
    artifact_types = {item["artifact_type"] for item in result.evidence_ledger.artifact_refs}
    assert {"workorder_suggestion", "workorder_pending_action", "workorder_draft"} <= artifact_types
    assert result.output_frame.answer_variant == "workorder_draft_ready"
    assert "需要补充设备、故障码或时间窗口" not in result.output_frame.final_answer
    for text in ["G120电机1", "A07089", "草稿", "人工确认", "未派发"]:
        assert text in result.output_frame.final_answer
    complete = result.complete_payload
    assert complete["rendered_answer"]["answer_variant"] == "workorder_draft_ready"
    assert complete["final_content"] == result.output_frame.final_answer
    assert complete["manual_confirmation"]["manual_confirmation_required"] is True
    assert complete["workorder_draft_payload"]["draft_only"] is True
    assert complete["workorder_draft_payload"]["dispatch_forbidden"] is True
    assert complete["workorder_draft_payload"]["approval_requirements"][0]["required"] is True
    assert "workorder.dispatch" in complete["workflow_policy"]["forbidden_tools"]


def test_fault_code_answer_uses_concise_structured_template_by_default() -> None:
    artifact = KnowledgeStepArtifact(
        success=True,
        query="A07089 是什么意思",
        fault_codes=["A07089"],
        fault_code_entries=[_fault_code_entry()],
    )

    frame = build_output_frame(status="completed", artifacts={"knowledge_artifact": artifact})

    assert frame.answer_variant == "knowledge_answer"
    assert "一句话解释：A07089：转换单位后不能激活功能块" in frame.final_answer
    assert "可能原因：尝试激活功能块。转换单位后不允许此操作。" in frame.final_answer
    assert "建议处理：将单位恢复到出厂设置。" in frame.final_answer
    assert "p0100" in frame.final_answer
    assert "来源：S120_故障手册.pdf，第 232 页" in frame.final_answer
    assert "- 传播：LOCAL" not in frame.final_answer
    assert "- 反应：无" not in frame.final_answer


def test_fault_code_answer_expands_manual_fields_when_requested() -> None:
    artifact = KnowledgeStepArtifact(
        success=True,
        query="A07089 详细点，给出手册字段",
        fault_codes=["A07089"],
        fault_code_entries=[_fault_code_entry()],
    )

    frame = build_output_frame(status="completed", artifacts={"knowledge_artifact": artifact})

    assert "详细手册信息：" in frame.final_answer
    assert "- 信息类别：参数设置 / 配置 / 调试过程出错 (18)" in frame.final_answer
    assert "- 传播：LOCAL" in frame.final_answer
    assert "- 反应：无" in frame.final_answer
    assert "- 应答：无" in frame.final_answer


def test_fault_code_answer_does_not_invent_missing_cause_or_remedy() -> None:
    artifact = KnowledgeStepArtifact(
        success=True,
        query="A07089 是什么意思",
        fault_codes=["A07089"],
        fault_code_entries=[_fault_code_entry(cause="", remedy="")],
    )

    frame = build_output_frame(status="completed", artifacts={"knowledge_artifact": artifact})

    assert "可能原因：手册未明确给出" in frame.final_answer
    assert "建议处理：手册未明确给出" in frame.final_answer


def test_fault_code_answer_warns_when_no_exact_match() -> None:
    artifact = KnowledgeStepArtifact(
        success=True,
        query="A07088 是什么意思",
        fault_codes=["A07089"],
        fault_code_entries=[_fault_code_entry(match_type="candidate_match")],
    )

    frame = build_output_frame(status="completed", artifacts={"knowledge_artifact": artifact})

    assert "未找到精确匹配：A07088。" in frame.final_answer
    assert "候选：" in frame.final_answer
    assert "A07089：单位转换： 转换单位后不能激活功能块" in frame.final_answer


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


def test_v2_artifact_projection_persists_manifests_and_case_snapshot() -> None:
    frame = build_output_frame(
        status="completed",
        artifacts={"analysis_artifact": _analysis(), "report_artifact": ReportStepArtifact(success=True, report_filename="demo.html", report_url="/reports/demo.html")},
        evidence_bundle=_bundle(),
    )

    envelope = project_artifact_envelope(
        thread_id="thread.manifest.output",
        output_frame=frame,
        evidence_bundle=_bundle(),
        artifacts={
            "analysis_artifact": _analysis(),
            "report_artifact": ReportStepArtifact(success=True, report_filename="demo.html", report_url="/reports/demo.html"),
            "structured_analysis_artifact": {
                "asset": "J1号机",
                "fault_code": "A07089",
                "diagnosis_summary": "J1号机存在 A07089 相关异常。",
                "severity": "medium",
            },
        },
        node_results=[],
        trace={"trace_id": "trace.manifest.output"},
        request_summary="诊断 J1 A07089",
    )

    manifests = envelope.payload["artifact_manifests"]
    assert any(item["artifact_type"] == "analysis_artifact" for item in manifests)
    assert any(item["artifact_type"] == "report_artifact" and item["report_url"] == "/reports/demo.html" for item in manifests)
    assert envelope.payload["latest_focus"]["artifact_type"] == "report_artifact"
    snapshot = envelope.payload["case_state_snapshot"]
    assert snapshot["schema_version"] == "case_state_snapshot.v1"
    assert snapshot["active_asset"] == "J1号机"
    assert snapshot["active_fault_codes"] == ["A07089"]
    assert snapshot["latest_artifact_type"] == "report_artifact"


def test_manifest_projection_extracts_fault_code_from_analysis_and_evidence_without_structured_payload() -> None:
    bundle = EvidenceBundle(
        bundle_id="bundle.analysis.codes",
        trace_id="trace.analysis.codes",
        evidence_items=[
            EvidenceItem(
                evidence_id="ev_codes",
                evidence_type="alarm_event",
                source_type="sql",
                source_name="real_data_01",
                summary="样本窗口内 A07089 持续出现。",
                metadata={"alarm_codes": ["A07089"]},
            )
        ],
        claims=[],
        final_claim_ids=[],
    )
    analysis = AnalysisStepArtifact(
        success=True,
        conclusion="G120电机1 最新记录事件码/告警码为 A07089，建议复核当前状态。",
        basis=["事件码/告警码统计：A07089。"],
        recommendations=["刷新当前状态。"],
        confidence="medium",
    )
    report = ReportStepArtifact(success=True, report_filename="g120.html", report_url="/reports/g120.html")

    envelope = project_artifact_envelope(
        thread_id="thread.analysis.codes",
        output_frame=build_output_frame(status="completed", artifacts={"analysis_artifact": analysis, "report_artifact": report}, evidence_bundle=bundle),
        evidence_bundle=bundle,
        artifacts={"analysis_artifact": analysis, "report_artifact": report},
        node_results=[],
        trace={"trace_id": "trace.analysis.codes"},
        request_summary="生成G120电机1的运行报告",
    )

    manifests = envelope.payload["artifact_manifests"]
    analysis_manifest = next(item for item in manifests if item["artifact_type"] == "analysis_artifact")
    report_manifest = next(item for item in manifests if item["artifact_type"] == "report_artifact")
    assert analysis_manifest["fault_code_refs"] == ["A07089"]
    assert report_manifest["fault_code_refs"] == ["A07089"]
    assert envelope.payload["case_state_snapshot"]["active_fault_codes"] == ["A07089"]


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
