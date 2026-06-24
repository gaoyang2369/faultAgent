from __future__ import annotations

import json

import pytest

from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.single_agent.reporting import (
    build_analysis_evidence_summary,
    build_report_payload,
    build_structured_analysis_artifact,
)
from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.output.renderers import render_final_answer
from fault_diagnosis.single_agent.sql_safety import REAL_DATA_FALLBACK_COLUMNS, REAL_DATA_LATEST_TABLE
from fault_diagnosis.tools.kb_tools import query_fault_code_from_local_pdfs
from fault_diagnosis.tools.report_tools import _build_report_html


def _operation_report_payload(**overrides) -> str:
    payload = {
        "title": "DCMA 运行诊断报告",
        "report_time": "2026-06-23 10:00:00",
        "asset": "J1号机",
        "report_type": "运行诊断报告",
        "data_window": "2026-06-10 12:10:00 ~ 2026-06-10 12:13:00",
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
        "one_sentence_conclusion": "采样窗口内，J1号机持续出现 A07089 事件；不代表当前实时状态。",
        "top_actions": ["重新获取实时数据或确认采样链路"],
        "kpi_cards": [],
        "findings": [],
        "cause_candidates": [],
        "action_plan": [],
        "workorder_suggestion": {"decision": "暂不创建维修工单", "trigger_conditions": []},
        "evidence_summary": [],
        "limitations": ["本报告仅用于辅助诊断。"],
        "appendix": {
            "sql_summary": "测试 SQL 摘要",
            "sql_query": "SELECT id, create_time FROM real_data_01 LIMIT 5",
            "trend_statistics": [],
            "raw_metric_tables": [],
            "knowledge_sources": [],
            "generation_metadata": {"report_time": "2026-06-23 10:00:00"},
        },
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_report_payload_renders_real_data_rows_as_tables() -> None:
    request = DiagnosisRequest(
        user_message="最近dcma运行情况如何？有异常码？可以生成具体报告展示",
        user_identity="游客",
        equipment_hint=None,
        metric_hint=None,
        fault_code_hint=None,
        time_range_hint="最近",
        needs_report=True,
        report_format="markdown",
        analysis_goal="生成运行状态报告",
    )
    sql = (
        f"SELECT {REAL_DATA_FALLBACK_COLUMNS} FROM {REAL_DATA_LATEST_TABLE} "
        f"WHERE 1=1 ORDER BY {REAL_DATA_LATEST_TABLE}.create_time DESC, id DESC LIMIT 50"
    )
    sql_artifact = SqlStepArtifact(
        success=True,
        summary=f"查询 {REAL_DATA_LATEST_TABLE} 最近 50 条运行状态、异常码和关键运行指标。",
        sql_used=[sql],
        raw_output=str(
            [
                (
                    566,
                    "2026/01/14 18:27:24",
                    "G120电机1",
                    "G120电机1",
                    "2026/01/14",
                    "18:27:24 000ms",
                    "45",
                    "F1030-0/0/0",
                    "0",
                    "5120",
                    "8384",
                    563.5,
                    0,
                    0,
                    0,
                    0,
                    0,
                    -200,
                    20.09,
                    23.3,
                    0,
                    0,
                    0,
                    "24.7",
                    20.09,
                    0,
                    0,
                    2,
                    0.44,
                    0,
                    "2026-01-14 18:27:24",
                )
            ]
        ),
    )
    analysis_artifact = AnalysisStepArtifact(
        success=True,
        conclusion="最近记录显示设备存在故障码，需要现场复核。",
        basis=[f"SQL 返回最新 {REAL_DATA_LATEST_TABLE} 行"],
        recommendations=["核对 F1030 对应手册含义"],
        confidence="medium",
    )
    payload = build_report_payload(
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=KnowledgeStepArtifact(success=False, query="", raw_output=""),
        analysis_artifact=analysis_artifact,
        current_time="2026-06-11 19:30:00",
        report_filename="test-report",
    )

    assert payload["title"] == "DCMA 运行诊断报告"
    assert set(payload) == {"title", "report_filename", "chart_payload", "operation_report_payload"}
    operation_report = json.loads(payload["operation_report_payload"])
    assert operation_report["report_type"] == "运行诊断报告"
    assert operation_report["event_code"] == "F1030-0/0/0"
    assert operation_report["appendix"]["sql_query"].startswith("SELECT")
    chart_payload = json.loads(payload["chart_payload"])
    assert chart_payload["source_table"] == REAL_DATA_LATEST_TABLE
    assert chart_payload["status_summary"]["status_level"] == "故障 / 需处理"
    assert chart_payload["status_summary"]["current_event"] == "F1030-0/0/0"
    assert chart_payload["trend_metrics"]
    assert chart_payload["data_quality"]["freshness_label"] == "已滞后"
    assert chart_payload["data_quality"]["metric_availability"] == "100%"
    assert {group["key"] for group in chart_payload["trend_groups"]} >= {"speed", "power_supply", "temperature", "load"}
    speed_group = next(group for group in chart_payload["trend_groups"] if group["key"] == "speed")
    assert {metric["key"] for metric in speed_group["metrics"]} == {"speed_setpoint", "speed_actual"}
    assert {metric["unit"] for metric in speed_group["metrics"]} == {"rpm"}
    load_group = next(group for group in chart_payload["trend_groups"] if group["key"] == "load")
    assert {"name": "关注", "value": 75, "unit": "%"} in load_group["thresholds"]
    assert chart_payload["latest_metric_groups"]
    assert chart_payload["fault_counts"] == [{"name": "F1030-0/0/0", "value": 1}]


def test_report_html_embeds_echarts_visualization() -> None:
    chart_payload = json.dumps(
        {
            "timestamps": ["2026-06-10 12:12:59"],
            "trend_groups": [
                {
                    "key": "speed",
                    "name": "速度跟随",
                    "metrics": [{"key": "speed_actual", "name": "实际转速", "unit": "rpm", "values": [442.2]}],
                    "thresholds": [],
                }
            ],
            "status_counts": [{"name": "42", "value": 1}],
            "fault_counts": [{"name": "A07089", "value": 1}],
            "latest_metric_groups": [
                {"key": "speed", "name": "速度跟随", "metrics": [{"name": "实际转速", "value": 442.2, "unit": "rpm"}]}
            ],
        },
        ensure_ascii=False,
    )

    html = _build_report_html(
        operation_report_payload=_operation_report_payload(),
        chart_payload=chart_payload,
    )

    assert "运行数据可视化" in html
    assert "dcma-trend-chart" in html
    assert "cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js" in html
    assert "A07089" in html


def test_report_html_formats_structured_sql_appendix() -> None:
    html = _build_report_html(
        operation_report_payload=_operation_report_payload(
            appendix={
                "sql_summary": "测试 SQL",
                "sql_query": "SELECT id, timestamp FROM real_data_01 ORDER BY create_time DESC LIMIT 50",
                "trend_statistics": [],
                "raw_metric_tables": [],
                "knowledge_sources": [],
                "generation_metadata": {"report_time": "2026-06-23 10:00:00"},
            }
        )
    )

    assert '<pre class="code-block">' in html
    assert "SELECT id, timestamp FROM real_data_01" in html


def test_report_html_rejects_missing_structured_payload() -> None:
    with pytest.raises(ValueError, match="缺少结构化运行诊断报告 payload"):
        _build_report_html(operation_report_payload="")


def test_report_html_renders_grouped_trends_and_metric_snapshot() -> None:
    chart_payload = json.dumps(
        {
            "timestamps": ["2026-06-10 12:12:56", "2026-06-10 12:12:59"],
            "trend_groups": [
                {
                    "key": "speed",
                    "name": "速度跟随",
                    "metrics": [
                        {"key": "speed_setpoint", "name": "给定转速", "unit": "rpm", "values": [820, 823]},
                        {"key": "speed_actual", "name": "实际转速", "unit": "rpm", "values": [450, 442]},
                    ],
                    "thresholds": [],
                },
                {
                    "key": "load",
                    "name": "负载率",
                    "metrics": [
                        {"key": "motor_load_rate", "name": "电机负载率", "unit": "%", "values": [76, 78]},
                    ],
                    "thresholds": [{"name": "关注", "value": 75, "unit": "%"}],
                },
            ],
            "status_counts": [{"name": "42", "value": 2}],
            "fault_counts": [{"name": "A07089", "value": 2}],
            "latest_metric_groups": [
                {
                    "key": "speed",
                    "name": "速度跟随",
                    "metrics": [
                        {"name": "给定转速", "value": 823.41, "unit": "rpm"},
                        {"name": "实际转速", "value": 442.21, "unit": "rpm"},
                    ],
                }
            ],
            "status_summary": {
                "status_level": "告警 / 需确认",
                "source_table": "real_data_01",
                "device": "G120电机1",
                "latest_sample_time": "2026-06-10 12:12:59",
                "sample_window": "2026-06-10 12:12:56 至 2026-06-10 12:12:59，2 条记录",
                "current_event": "A07089",
                "key_phenomenon": "速度给定与实际速度偏差 46.3%",
                "priority": "中",
                "initial_assessment": "存在参数/配置/调试相关事件迹象。",
                "next_action": "先确认运行模式和参数变更。",
            },
            "data_quality": {
                "latest_sample_time": "2026-06-10 12:12:59",
                "sample_count": 2,
                "freshness_label": "实时性良好",
                "metric_availability": "100%",
                "currentness": "可作为当前状态的强参考",
            },
        },
        ensure_ascii=False,
    )

    html = _build_report_html(
        operation_report_payload=_operation_report_payload(),
        chart_payload=chart_payload,
    )

    assert "速度跟随" in html
    assert "负载率" in html
    assert "dcma-trend-chart-1" in html
    assert "数据质量摘要" in html
    assert "一页结论" in html
    assert "报告类型" in html
    assert "metric-groups" in html
    assert "823.41 rpm" in html
    assert "renderTrendGroup" in html


def test_status_report_downgrades_a_code_to_warning_event() -> None:
    request = DiagnosisRequest(
        user_message="生成 DCMA 当前运行状态报告",
        user_identity="游客",
        equipment_hint=None,
        metric_hint=None,
        fault_code_hint=None,
        time_range_hint="当前",
        needs_report=True,
        report_format="markdown",
        analysis_goal="生成运行状态报告",
    )
    row = (
        566,
        "2026/06/10 12:12:59",
        "G120电机1",
        "G120电机1",
        "2026/06/10",
        "12:12:59 000ms",
        "42",
        "A07089",
        "0",
        "5246",
        "10679",
        555.228,
        823.412,
        442.209,
        0.775,
        0,
        0,
        25.2,
        46.811,
        31.123,
        0.018,
        0.12,
        0.18,
        "24.7",
        31.123,
        12.0,
        18.0,
        2,
        0.44,
        0,
        "2026-06-10 12:12:59",
    )
    knowledge = KnowledgeStepArtifact(
        success=True,
        query="A07089 原因 处理",
        raw_output=(
            "来源：S120_故障手册.pdf\n"
            "页码：232\n"
            "A07089 单位转换：转换单位后不能激活功能块\n"
            "反应：无\n"
            "应答：无"
        ),
    )
    sql_artifact = SqlStepArtifact(success=True, summary="ok", raw_output=str([row] * 3))
    artifact = build_structured_analysis_artifact(
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge,
    )
    assert artifact is not None
    assert "告警 / 需确认" in artifact.conclusion
    assert "事件码/告警码" in artifact.conclusion
    assert not any("立即停机" in item or "严重故障" in item for item in artifact.recommendations)
    assert any("参数/配置检查" in item for item in artifact.recommendations)

    payload = build_report_payload(
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge,
        analysis_artifact=artifact,
        current_time="2026-06-23 12:13:00",
        report_filename="dcma-status",
    )
    chart_payload = json.loads(payload["chart_payload"])
    operation_report = json.loads(payload["operation_report_payload"])
    speed_group = next(group for group in chart_payload["trend_groups"] if group["key"] == "speed")
    assert chart_payload["status_summary"]["status_level"] == "告警 / 需确认"
    assert chart_payload["status_summary"]["current_event"] == "A07089"
    assert operation_report["severity"] == "warning"
    assert operation_report["severity_label"] == "WARNING / 采样窗口异常"
    assert operation_report["asset_risk_level"] == "warning"
    assert operation_report["data_currentness_level"] == "stale"
    assert operation_report["action_priority"] == "P1"
    assert operation_report["event_code"] == "A07089"
    assert operation_report["top_actions"][:1] == ["重新获取实时数据或确认采样链路"]
    assert operation_report["data_age_text"].endswith("天")
    assert "约 已滞后" not in operation_report["data_freshness_note"]
    assert "采样窗口内" in operation_report["one_sentence_conclusion"]
    assert "当前处于告警状态" not in operation_report["one_sentence_conclusion"]
    assert len(operation_report["findings"]) <= 5
    assert len(operation_report["cause_candidates"]) <= 3
    assert len(operation_report["action_plan"]) <= 5
    assert operation_report["action_plan"][0]["acceptance_criteria"]
    assert operation_report["action_plan"][0]["escalation_condition"]
    assert operation_report["workorder_suggestion"]["trigger_conditions"]
    assert "speed_error_rate" in {metric["key"] for metric in speed_group["metrics"]}
    assert {"name": "关注", "value": 20.0, "unit": "%"} in speed_group["thresholds"]

    html = _build_report_html(
        operation_report_payload=payload["operation_report_payload"],
        chart_payload=payload["chart_payload"],
    )
    assert "一页结论" in html
    assert "运行快照" in html
    assert "设备风险" in html
    assert "数据时效" in html
    assert "处置优先级" in html
    assert "趋势与持续性" in html
    assert "原因候选" in html
    assert "处置计划" in html
    assert "附录" in html
    assert "SQL 与执行信息" in html
    assert "报告生成元信息" in html
    assert "Executive Diagnosis" not in html
    assert "Operational Snapshot" not in html
    assert "CRITICAL / 严重" not in html


def test_structured_analysis_avoids_stale_timestamp_language() -> None:
    request = DiagnosisRequest(
        user_message="最近dcma运行情况如何？有异常码？可以生成具体报告展示",
        user_identity="游客",
        equipment_hint=None,
        metric_hint=None,
        fault_code_hint=None,
        time_range_hint="最近",
        needs_report=True,
        report_format="markdown",
        analysis_goal="生成运行状态报告",
    )
    row = (
        566,
        "1768386444814",
        "G120电机1",
        "G120电机1",
        "2026/01/14",
        "18:27:24 492ms",
        "45",
        "F1030-0/0/0",
        "0",
        "5246",
        "8784",
        563.5,
        0,
        0,
        0,
        0,
        0,
        -200,
        20.09,
        23.3,
        0,
        0,
        0,
        "0",
        23.3,
        0,
        0,
        2,
        0.47,
        0,
        "2026-01-14 18:27:24",
    )
    artifact = build_structured_analysis_artifact(
        request=request,
        sql_artifact=SqlStepArtifact(success=True, summary="ok", raw_output=str([row])),
        knowledge_artifact=KnowledgeStepArtifact(
            success=True,
            query="F1030",
            raw_output="来源：基础PDF知识库\n文档片段：F1030 示例知识片段",
        ),
    )

    assert artifact is not None
    assert "F1030" in artifact.conclusion
    assert "自动补充知识库检索结果用于诊断说明" in artifact.conclusion
    assert "无法确认当前实时状态" not in artifact.conclusion
    assert "数据时间戳" not in artifact.conclusion
    assert artifact.confidence == "high"


def test_structured_analysis_uses_rag_fault_code_actions() -> None:
    request = DiagnosisRequest(
        user_message="最近设备报 F01002，帮我诊断并给出处置建议",
        user_identity="游客",
        equipment_hint="G120电机1",
        metric_hint=None,
        fault_code_hint="F01002",
        time_range_hint="最近",
        needs_report=False,
        report_format="markdown",
        analysis_goal="诊断 F01002",
    )
    row = (
        566,
        "2026/01/14 18:27:24",
        "G120电机1",
        "G120电机1",
        "2026/01/14",
        "18:27:24 000ms",
        "45",
        "F01002",
        "0",
        "5120",
        "8384",
        563.5,
        0,
        0,
        0,
        0,
        0,
        -200,
        20.09,
        23.3,
        0,
        0,
        0,
        "24.7",
        20.09,
        0,
        0,
        2,
        0.44,
        0,
        "2026-01-14 18:27:24",
    )
    knowledge_output = query_fault_code_from_local_pdfs("F01002 原因 处理")
    knowledge_artifact = KnowledgeStepArtifact(
        success=True,
        query="F01002 原因 处理",
        raw_output=knowledge_output,
    )
    artifact = build_structured_analysis_artifact(
        request=request,
        sql_artifact=SqlStepArtifact(success=True, summary="ok", raw_output=str([row])),
        knowledge_artifact=knowledge_artifact,
    )
    evidence_summary = build_analysis_evidence_summary(
        request=request,
        sql_artifact=SqlStepArtifact(success=True, summary="ok", raw_output=str([row])),
        knowledge_artifact=knowledge_artifact,
    )

    assert artifact is not None
    assert any("故障码处置：按 RAG 手册片段核对触发条件和处理项" in item for item in artifact.recommendations)
    assert any("重新为所有组件上电" in item for item in artifact.recommendations)
    assert any("RAG 处置要点" in item for item in artifact.basis)
    assert any("故障码主因优先按 RAG 手册核对" in item for item in artifact.probable_causes)
    assert any("状态字、控制字" in item for item in artifact.verification_items)
    assert any("事件码识别：high" in item for item in artifact.confidence_details)
    assert not any("演示后建议" in item for item in artifact.recommendations)
    assert "RAG知识要点" in evidence_summary
    assert "F01002" in evidence_summary


def test_final_answer_uses_task_template_without_fallback_sections() -> None:
    artifact = AnalysisStepArtifact(
        success=True,
        conclusion="设备存在异常码，需结合数据和 RAG 处理。",
        basis=["SQL 返回最新运行数据", "RAG 命中故障码处理步骤"],
        probable_causes=["RAG 指向参数配置问题"],
        verification_items=["确认复位后是否复现"],
        recommendations=["立即处置：按现场规程确认安全状态后处理"],
        risk_notice="处置前确认现场安全状态。",
        missing_information=["现场是否已复位"],
        confidence_details=["异常码识别：high", "处置闭环：medium"],
        confidence="medium",
    )

    rendered = render_final_answer(
        decision=SingleAgentDecision(primary_task_type="fault_diagnosis"),
        evidence_bundle=None,
        analysis_artifact=artifact,
    )
    answer = rendered.content

    assert "【报告文件】" not in answer
    assert "诊断结论" in answer
    assert "处置建议" in answer
    assert "关键证据" in answer
    assert "可能原因" in answer
    assert "RAG 指向参数配置问题" in answer
    assert "【可能原因与待验证】" not in answer
    assert "【建议处置与验证】" not in answer
