from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.single_agent.reporting import build_report_payload
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
    sql = f"SELECT {REAL_DATA_FALLBACK_COLUMNS} FROM real_data WHERE 1=1 ORDER BY create_time DESC, id DESC LIMIT 50"
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
    assert "不能等同于当前实时状态" in payload["executive_summary"]
    assert "### 最新运行快照" in payload["diagnosis_details"]
    assert "数据新鲜度提示" in payload["diagnosis_details"]
    assert "| 时间 | 设备 | 状态 | 故障码 | 告警码" in payload["diagnosis_details"]
    assert "G120电机1" in payload["diagnosis_details"]
    assert "F1030-0/0/0" in payload["fault_inference"]
