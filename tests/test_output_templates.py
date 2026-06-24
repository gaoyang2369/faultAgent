from __future__ import annotations

import json

from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    Claim,
    ClaimConfidence,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.evidence.quality import build_output_guardrail_result
from fault_diagnosis.single_agent.output.renderers import render_final_answer
from fault_diagnosis.tools.report_tools import _build_report_html


def _evidence_bundle() -> EvidenceBundle:
    items = [
        EvidenceItem(
            evidence_id="ev_sql_status",
            evidence_type="device_status",
            source_type="sql",
            source_name="real_data_01",
            summary="J1号机最近 30 分钟有运行记录，温度指标进入关注区间。",
            quality=EvidenceQuality(reliability="high", freshness="recent", relevance="high", completeness="partial"),
        ),
        EvidenceItem(
            evidence_id="ev_sql_alarm",
            evidence_type="alarm_event",
            source_type="sql",
            source_name="real_data_01",
            summary="告警记录出现 F01002，当前状态需要结合实时告警确认。",
            quality=EvidenceQuality(reliability="high", freshness="recent", relevance="high", completeness="partial"),
        ),
        EvidenceItem(
            evidence_id="ev_kb_001",
            evidence_type="manual_reference",
            source_type="knowledge_base",
            source_name="故障手册",
            summary="知识库说明 F01002 通常与过温或散热异常相关。",
            quality=EvidenceQuality(reliability="high", freshness="unknown", relevance="high", completeness="partial"),
        ),
    ]
    claims = [
        Claim(
            claim_id="claim_diagnosis_summary",
            claim_type="diagnosis_summary",
            statement="当前更倾向于散热异常/过温保护导致的故障风险，但不能判定为硬件损坏。",
            confidence=ClaimConfidence(level="medium", score=0.66),
            supporting_evidence_ids=["ev_sql_status", "ev_sql_alarm", "ev_kb_001"],
            missing_evidence=["更长时间窗口的温度趋势", "维修历史"],
            status="final",
        ),
        Claim(
            claim_id="claim_root_cause_001",
            claim_type="root_cause_candidate",
            statement="散热风道堵塞或风扇异常。",
            confidence=ClaimConfidence(level="medium", score=0.64),
            supporting_evidence_ids=["ev_sql_status", "ev_kb_001"],
            missing_evidence=["现场散热检查记录"],
        ),
        Claim(
            claim_id="claim_recommendation",
            claim_type="recommendation",
            statement="优先检查风扇、风道和环境温度。",
            confidence=ClaimConfidence(level="medium", score=0.62),
            supporting_evidence_ids=["ev_sql_status", "ev_kb_001"],
        ),
        Claim(
            claim_id="claim_workorder_decision",
            claim_type="workorder_decision",
            statement="建议生成检修工单。",
            confidence=ClaimConfidence(level="medium", score=0.7),
            supporting_evidence_ids=["ev_sql_status", "ev_sql_alarm"],
            decision="suggest_create",
        ),
    ]
    bundle = EvidenceBundle(
        bundle_id="bundle.test",
        trace_id="trace.test",
        task={"task_type": "fault_diagnosis"},
        evidence_items=items,
        claims=claims,
        final_claim_ids=["claim_diagnosis_summary"],
        quality_checks={
            "all_claims_have_evidence": True,
            "no_dangling_evidence_refs": True,
            "missing_evidence_disclosed": True,
        },
    )
    return bundle


def _analysis() -> AnalysisStepArtifact:
    return AnalysisStepArtifact(
        success=True,
        conclusion="当前更倾向于散热异常/过温保护导致的故障风险，但不能判定为硬件损坏。",
        basis=["温度指标进入关注区间", "出现 F01002 告警", "知识库提示过温或散热异常"],
        probable_causes=["散热风道堵塞或风扇异常", "负载升高导致温升"],
        recommendations=["优先检查风扇、风道和环境温度", "如果告警持续或重复出现，建议生成检修工单"],
        risk_notice="告警持续时按中高风险处理。",
        missing_information=["更长时间窗口的温度趋势", "维修历史"],
        confidence="medium",
    )


def _workorder() -> WorkOrderSuggestion:
    return WorkOrderSuggestion(
        need_workorder=True,
        reason="告警持续且温度进入关注区间",
        priority="P1",
        priority_label="中高优先级",
        title="J1号机 F01002 温度异常检修",
    )


def test_fault_diagnosis_template_contains_required_sections_and_evidence() -> None:
    rendered = render_final_answer(
        decision=SingleAgentDecision(primary_task_type="fault_diagnosis"),
        evidence_bundle=_evidence_bundle(),
        analysis_artifact=_analysis(),
        workorder_suggestion=_workorder(),
        report_artifact=ReportStepArtifact(success=False, save_result="未生成报告"),
    )

    section_titles = [section.title for section in rendered.sections]
    assert section_titles == ["诊断结论", "当前状态", "关键证据", "可能原因", "处置建议", "工单建议", "证据不足说明"]
    assert "诊断结论" in rendered.content
    assert "证据不足说明" in rendered.content
    assert rendered.used_evidence_ids
    assert all(section.evidence_ids for section in rendered.sections if section.key != "limitations")


def test_alarm_triage_template_answers_current_alarm_status() -> None:
    rendered = render_final_answer(
        decision=SingleAgentDecision(primary_task_type="alarm_triage"),
        evidence_bundle=_evidence_bundle(),
        analysis_artifact=_analysis(),
        knowledge_artifact=KnowledgeStepArtifact(
            success=True,
            query="F01002 含义",
            snippets=["F01002 通常与过温或散热异常相关。"],
        ),
    )

    assert [section.title for section in rendered.sections] == [
        "告警解释",
        "当前告警状态",
        "严重程度判断",
        "处置建议",
        "证据不足说明",
    ]
    assert "当前告警状态" in rendered.content
    assert "不能确认" in rendered.content or "当前状态" in rendered.content


def test_action_request_template_keeps_safety_boundary() -> None:
    rendered = render_final_answer(
        decision=SingleAgentDecision(primary_task_type="action_request", action_type="重启设备"),
        evidence_bundle=None,
        analysis_artifact=_analysis(),
    )

    assert "我不能直接执行" in rendered.content
    assert "已重启" not in rendered.content
    assert "已派发工单" not in rendered.content
    guardrail = build_output_guardrail_result(rendered.content, None, SingleAgentDecision(primary_task_type="action_request"), rendered)
    assert guardrail["passed"] is True


def test_report_generation_template_returns_summary_and_link_only() -> None:
    rendered = render_final_answer(
        decision=SingleAgentDecision(primary_task_type="report_generation"),
        evidence_bundle=_evidence_bundle(),
        analysis_artifact=_analysis(),
        report_artifact=ReportStepArtifact(
            success=True,
            report_filename="j1_report.html",
            report_title="J1号机运行诊断报告",
            report_url="/reports/j1_report.html",
            save_result="/reports/j1_report.html",
        ),
    )

    assert "报告状态：报告已生成。" in rendered.content
    assert "报告标题：J1号机运行诊断报告" in rendered.content
    assert "报告摘要" in rendered.content
    assert "报告链接：/reports/j1_report.html" in rendered.content
    assert "证据不足提示" in rendered.content


def test_report_html_uses_structured_report_chapters() -> None:
    operation_report_payload = json.dumps(
        {
            "title": "DCMA 运行诊断报告",
            "report_time": "2026-06-23 10:00:00",
            "asset": "J1号机",
            "report_type": "运行诊断报告",
            "data_window": "2026-06-10 12:10:00 ~ 12:13:00",
            "sample_count": 3,
            "data_age_text": "13 天",
            "data_freshness_label": "已滞后",
            "data_freshness_note": "最新样本距报告时间约 13 天，数据已滞后，仅代表采样窗口。",
            "data_currentness_level": "stale",
            "data_currentness_label": "STALE / 不代表实时状态",
            "asset_risk_level": "warning",
            "asset_risk_label": "WARNING / 采样窗口异常",
            "action_priority": "P1",
            "action_priority_label": "立即确认实时数据与现场状态",
            "confidence_level": "中",
            "severity": "warning",
            "severity_label": "WARNING / 采样窗口异常",
            "confidence": "中",
            "event_code": "A07089",
            "one_sentence_conclusion": "采样窗口内，J1号机持续出现 A07089 事件。",
            "top_actions": ["重新获取实时数据或确认采样链路"],
            "kpi_cards": [],
            "findings": [],
            "cause_candidates": [],
            "action_plan": [],
            "workorder_suggestion": {"decision": "暂不创建维修工单", "trigger_conditions": []},
            "evidence_summary": [],
            "limitations": ["本报告仅用于辅助诊断。"],
            "appendix": {
                "sql_summary": "测试 SQL",
                "sql_query": "SELECT 1",
                "trend_statistics": [],
                "raw_metric_tables": [],
                "knowledge_sources": [],
                "generation_metadata": {"report_time": "2026-06-23 10:00:00"},
            },
        },
        ensure_ascii=False,
    )
    html = _build_report_html(
        operation_report_payload=operation_report_payload,
    )

    for title in (
        "一页结论",
        "运行快照",
        "关键发现",
        "原因候选",
        "处置计划",
        "工单建议",
        "证据与边界",
        "附录",
    ):
        assert title in html
