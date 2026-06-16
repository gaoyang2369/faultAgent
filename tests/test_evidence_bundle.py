from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    EvidenceItem,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from fault_diagnosis.runtime.diagnosis_contract_adapter import build_diagnosis_contract_payload
from fault_diagnosis.single_agent.artifacts import build_diagnosis_artifact_envelope
from fault_diagnosis.single_agent.contracts import AgentTrace, SingleAgentDecision
from fault_diagnosis.single_agent.contracts import SingleAgentLimits
from fault_diagnosis.single_agent.evidence import build_evidence_bundle, build_tool_evidence_preview


def _request() -> DiagnosisRequest:
    return DiagnosisRequest(
        user_message="最近dcma运行情况如何？有异常码？",
        user_identity="游客",
        equipment_hint=None,
        metric_hint=None,
        fault_code_hint=None,
        time_range_hint="最近",
        needs_report=True,
        report_format="markdown",
        analysis_goal="诊断 DCMA 最近运行异常",
    )


def _sql_artifact() -> SqlStepArtifact:
    row = (
        566,
        "2026/06/16 13:20:00",
        "G120电机1",
        "G120电机1",
        "2026/06/16",
        "13:20:00 000ms",
        "45",
        "0",
        "A07089",
        "5120",
        "8384",
        563.5,
        820,
        440,
        20.1,
        0,
        0,
        25.0,
        72.0,
        60.0,
        12.2,
        0,
        0,
        "24.7",
        62.0,
        78.47,
        77.2,
        2,
        0.44,
        0,
        "2026-06-16 13:20:00",
    )
    return SqlStepArtifact(
        success=True,
        summary="查询 real_data_01 最近 50 条运行状态、异常码和关键运行指标。",
        sql_used=["SELECT ... FROM real_data_01 ORDER BY create_time DESC LIMIT 50"],
        raw_output=str([row, row, row]),
    )


def _knowledge_artifact() -> KnowledgeStepArtifact:
    raw_output = (
        "故障码：A07089\n"
        "来源：基础PDF知识库\n"
        "来源文件：S120_故障手册.pdf\n"
        "source_type：knowledge_base\n"
        "来源页码：12\n"
        "文档片段：A07089 单位转换激活异常。处理：恢复单位参数后重新激活功能块。"
    )
    return KnowledgeStepArtifact(
        success=True,
        query="A07089 含义 触发原因 处理步骤",
        snippets=[raw_output],
        raw_output=raw_output,
    )


def _analysis_artifact() -> AnalysisStepArtifact:
    return AnalysisStepArtifact(
        success=True,
        conclusion="DCMA 最近样本存在 A07089 事件，速度偏差和负载率进入关注区间。",
        basis=["SQL 返回最近运行记录", "知识库返回 A07089 处理片段"],
        probable_causes=["单位参数或功能块激活状态需要复核。"],
        verification_items=["复核参数变更记录", "核对运行使能和反馈链路"],
        recommendations=["备份当前参数快照后按手册核对单位参数。"],
        risk_notice="事件未闭环前避免反复改参或复位。",
        missing_information=["现场参数变更记录"],
        confidence_details=["事件码识别：high", "RAG 释义匹配：high"],
        confidence="high",
    )


def _workorder_suggestion() -> WorkOrderSuggestion:
    return WorkOrderSuggestion(
        need_workorder=True,
        reason="A07089 事件持续存在；速度偏差超过关注阈值；负载率进入关注区间",
        workorder_type="参数复核 / 运行异常排查",
        priority="P1",
        priority_label="中优先级",
        risk_level="中",
        assignee_role="电气维护人员",
        suggested_completion_window="24小时内",
        diagnosis_conclusion="A07089 相关知识库提示：恢复单位参数后重新激活功能块。",
        key_evidence=["最近 3 条均出现 A07089", "速度偏差 46.34%", "负载率 78.47%"],
        processing_steps=["备份当前参数快照", "核查单位制相关参数"],
        acceptance_criteria=["A07089 不再持续出现"],
        equipment_object="DCMA / G120电机1",
        fault_code="A07089",
        title="DCMA / G120电机1 A07089 事件及速度偏差排查",
    )


def test_evidence_item_keeps_legacy_display_fields() -> None:
    item = EvidenceItem(source_type="sql", title="SQL 查询摘要", content="返回 3 条运行记录")

    assert item.summary == "返回 3 条运行记录"
    assert item.title == "SQL 查询摘要"
    assert item.evidence_type == "generic"


def test_build_evidence_bundle_links_claims_to_existing_evidence() -> None:
    bundle = build_evidence_bundle(
        trace_id="trace.test",
        request=_request(),
        decision=SingleAgentDecision(needs_sql=True, needs_knowledge=True, needs_report=True),
        sql_artifact=_sql_artifact(),
        knowledge_artifact=_knowledge_artifact(),
        analysis_artifact=_analysis_artifact(),
        workorder_suggestion=_workorder_suggestion(),
        report_artifact=ReportStepArtifact(success=True, report_filename="report.html"),
    )

    evidence_ids = {item.evidence_id for item in bundle.evidence_items}
    assert {"ev_user_request", "ev_sql_event_codes", "ev_kb_001"} <= evidence_ids
    assert bundle.quality_checks["no_dangling_evidence_refs"] is True
    assert bundle.quality_checks["all_claims_have_evidence"] is True
    assert bundle.quality_checks["missing_evidence_disclosed"] is True
    assert all(set(claim.supporting_evidence_ids) <= evidence_ids for claim in bundle.claims)
    assert any(
        claim.claim_type == "workorder_decision" and claim.decision == "suggest_create"
        for claim in bundle.claims
    )


def test_artifact_contract_exports_bundle_evidence_and_claim_findings() -> None:
    request = _request()
    decision = SingleAgentDecision(needs_sql=True, needs_knowledge=True, needs_report=True)
    sql_artifact = _sql_artifact()
    knowledge_artifact = _knowledge_artifact()
    analysis_artifact = _analysis_artifact()
    workorder_suggestion = _workorder_suggestion()
    report_artifact = ReportStepArtifact(success=True, report_filename="report.html")
    bundle = build_evidence_bundle(
        trace_id="trace.test",
        request=request,
        decision=decision,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
        report_artifact=report_artifact,
    )
    envelope = build_diagnosis_artifact_envelope(
        thread_id="thread.test",
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
        report_artifact=report_artifact,
        final_answer="最终答复",
        decision=decision,
        trace=AgentTrace(
            trace_id="trace.test",
            request_id="request.test",
            thread_id="thread.test",
            user_identity="游客",
            user_message=request.user_message,
        ),
        evidence_bundle=bundle,
        output_guardrail={"passed": True, "warnings": []},
    )

    payload = build_diagnosis_contract_payload(envelope)

    assert envelope.payload["evidence_bundle"]["bundle_id"] == bundle.bundle_id
    assert payload["evidence_count"] == len(bundle.evidence_items)
    assert any(item["id"] == "ev_sql_event_codes" for item in payload["evidences"])
    assert any(item["id"] == "claim_diagnosis_summary" for item in payload["findings"])
    assert any("ev_sql_event_codes" in item["evidence_ids"] for item in payload["findings"])


def test_tool_evidence_preview_is_compact() -> None:
    preview_items = build_tool_evidence_preview(
        tool_name="sql_db_query",
        output=_sql_artifact().raw_output,
    )

    assert preview_items
    assert all("summary" in item for item in preview_items)
    assert "raw_output" not in preview_items[0]


def test_default_stage_limit_covers_full_report_workflow() -> None:
    full_report_stages = [
        "understand",
        "initialize_evidence_bundle",
        "sql",
        "knowledge",
        "analysis",
        "workorder_decision",
        "report",
        "evidence_validation",
        "final_answer",
        "output_guardrail",
        "save_artifact",
    ]

    assert SingleAgentLimits().max_rounds >= len(full_report_stages)
