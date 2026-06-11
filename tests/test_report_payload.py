from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.single_agent.reporting import build_report_payload, build_structured_analysis_artifact
from fault_diagnosis.single_agent.sql_safety import REAL_DATA_FALLBACK_COLUMNS


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
    sql = f"SELECT {REAL_DATA_FALLBACK_COLUMNS} FROM real_data WHERE 1=1 ORDER BY real_data.create_time DESC, id DESC LIMIT 50"
    sql_artifact = SqlStepArtifact(
        success=True,
        summary="查询 real_data 最近 50 条运行状态、异常码和关键运行指标。",
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
        basis=["SQL 返回最新 real_data 行"],
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

    assert "已获取 1 条 DCMA 运行数据" in payload["executive_summary"]
    assert "不能等同于当前实时状态" not in payload["executive_summary"]
    assert "数据时间戳" not in payload["executive_summary"]
    assert "### 指标趋势可视化" in payload["diagnosis_details"]
    assert "### 最新运行快照" in payload["diagnosis_details"]
    assert "### 状态分布" in payload["diagnosis_details"]
    assert "### 故障码分布" in payload["diagnosis_details"]
    assert "| 时间 | 设备 | 状态 | 故障码 | 告警码" in payload["diagnosis_details"]
    assert "G120电机1" in payload["diagnosis_details"]
    assert "F1030-0/0/0" in payload["fault_inference"]


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
    assert "自动补充知识库检索结果" in artifact.conclusion
    assert "无法确认当前实时状态" not in artifact.conclusion
    assert "数据时间戳" not in artifact.conclusion
    assert artifact.confidence == "high"
