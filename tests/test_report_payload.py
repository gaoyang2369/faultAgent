from __future__ import annotations

import json

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
from fault_diagnosis.single_agent.final_answer import build_final_answer_fallback
from fault_diagnosis.single_agent.sql_safety import REAL_DATA_FALLBACK_COLUMNS, REAL_DATA_LATEST_TABLE
from fault_diagnosis.tools.kb_tools import query_fault_code_from_local_pdfs
from fault_diagnosis.tools.report_tools import _build_report_html


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

    assert f"已从 {REAL_DATA_LATEST_TABLE} 获取 1 条 DCMA 运行数据" in payload["executive_summary"]
    assert "不能等同于当前实时状态" not in payload["executive_summary"]
    assert "数据时间戳" not in payload["executive_summary"]
    assert "real_data_01" in payload["executive_summary"]
    assert "### 运行健康判定" in payload["diagnosis_details"]
    assert "### 异常特征解读" in payload["diagnosis_details"]
    assert "### 指标趋势可视化" in payload["diagnosis_details"]
    assert "### 最新运行快照" in payload["diagnosis_details"]
    assert "### 状态分布" in payload["diagnosis_details"]
    assert "### 故障码分布" in payload["diagnosis_details"]
    assert "| 时间 | 设备 | 状态 | 故障码 | 告警码" in payload["diagnosis_details"]
    assert "G120电机1" in payload["diagnosis_details"]
    assert "F1030-0/0/0" in payload["fault_inference"]
    chart_payload = json.loads(payload["chart_payload"])
    assert chart_payload["source_table"] == REAL_DATA_LATEST_TABLE
    assert chart_payload["trend_metrics"]
    assert chart_payload["fault_counts"] == [{"name": "F1030-0/0/0", "value": 1}]


def test_report_html_embeds_echarts_visualization() -> None:
    chart_payload = json.dumps(
        {
            "timestamps": ["2026-06-10 12:12:59"],
            "trend_metrics": [{"key": "dc_voltage", "name": "母线电压(V)", "values": [555.2]}],
            "status_counts": [{"name": "42", "value": 1}],
            "fault_counts": [{"name": "A07089", "value": 1}],
            "latest_metrics": [{"name": "实际转速", "value": 442.2}],
        },
        ensure_ascii=False,
    )

    html = _build_report_html(
        title="DCMA 故障诊断报告",
        report_time="2026-06-11 20:00:00",
        diagnosis_object="DCMA 系统",
        diagnosis_type="故障诊断",
        executive_summary="摘要",
        diagnosis_overview="概述",
        diagnosis_details="详情",
        fault_inference="推断",
        repair_recommendations="- 建议",
        preventive_maintenance="- 维护",
        diagnosis_basis="依据",
        chart_payload=chart_payload,
    )

    assert "运行数据可视化" in html
    assert "dcma-trend-chart" in html
    assert "cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js" in html
    assert "A07089" in html


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
    assert any("异常码主因优先按 RAG 手册核对" in item for item in artifact.probable_causes)
    assert any("状态字、控制字" in item for item in artifact.verification_items)
    assert any("异常码识别：high" in item for item in artifact.confidence_details)
    assert not any("演示后建议" in item for item in artifact.recommendations)
    assert "RAG知识要点" in evidence_summary
    assert "F01002" in evidence_summary


def test_final_answer_omits_report_section_when_report_not_generated() -> None:
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

    answer = build_final_answer_fallback(artifact)

    assert "【报告文件】" not in answer
    assert "未生成" not in answer
    assert "【优先动作】" in answer
    assert "【关键依据】" in answer
    assert "SQL 返回最新运行数据" in answer
    assert "RAG 指向参数配置问题" not in answer
    assert "【可能原因与待验证】" not in answer
    assert "【建议处置与验证】" not in answer
    assert "异常码识别 high" in answer
