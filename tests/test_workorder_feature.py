from __future__ import annotations

from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisRequest,
    KnowledgeStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.repositories.workorder_repository import FileWorkOrderRepository
from fault_diagnosis.services.workorder_service import CreateWorkOrderPayload, WorkOrderService
from fault_diagnosis.single_agent.reporting import build_workorder_suggestion


def _a07089_row(row_id: int) -> tuple:
    return (
        row_id,
        "2026/06/12 10:00:00",
        "G120电机1",
        "G120电机1",
        "2026/06/12",
        "10:00:00 000ms",
        "45",
        "A07089",
        "0",
        "5120",
        "8384",
        563.5,
        823.41,
        442.21,
        12.4,
        0,
        0,
        0,
        24.7,
        20.09,
        0,
        0,
        0,
        "24.7",
        20.09,
        78.47,
        78.47,
        2,
        0.44,
        0,
        "2026-06-12 10:00:00",
    )


def test_workorder_suggestion_triggers_for_persistent_a07089() -> None:
    request = DiagnosisRequest(
        user_message="诊断 G120电机1 A07089",
        user_identity="游客",
        equipment_hint="DCMA / G120电机1",
        metric_hint=None,
        fault_code_hint="A07089",
        time_range_hint="最近",
        needs_report=True,
        report_format="markdown",
        analysis_goal="故障诊断",
    )
    sql_artifact = SqlStepArtifact(
        success=True,
        summary="查询最近 50 条运行数据",
        sql_used=["SELECT ... LIMIT 50"],
        raw_output=str([_a07089_row(index) for index in range(50)]),
    )
    knowledge_artifact = KnowledgeStepArtifact(
        success=True,
        query="A07089 含义 处理",
        raw_output="故障码：A07089\n含义：单位制/功能块激活相关事件\n处理：检查单位制参数并重新激活功能块",
    )
    analysis_artifact = AnalysisStepArtifact(
        success=True,
        conclusion="A07089 持续存在，且速度偏差和负载率偏高。",
        basis=[],
        confidence="high",
    )

    suggestion = build_workorder_suggestion(
        request=request,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
    )

    assert suggestion.need_workorder is True
    assert suggestion.priority == "P1"
    assert suggestion.risk_level == "中"
    assert suggestion.fault_code == "A07089"
    assert "速度偏差" in "；".join(suggestion.key_evidence)
    assert "负载率" in "；".join(suggestion.key_evidence)
    assert "复核速度反馈链路" in suggestion.processing_steps
    assert "A07089 不再复现" in suggestion.acceptance_criteria


def test_file_workorder_repository_creates_trace_bound_record(tmp_path) -> None:
    service = WorkOrderService(repository=FileWorkOrderRepository(root_dir=tmp_path))
    response = service.create_work_order(
        CreateWorkOrderPayload(
            title="DCMA-G120电机1 A07089 事件及速度偏差排查",
            equipment_object="DCMA / G120电机1",
            fault_code="A07089",
            workorder_type="参数复核 / 运行异常排查",
            priority="P1",
            risk_level="中",
            diagnosis_conclusion="A07089 持续存在，且速度偏差和负载率偏高。",
            key_evidence=["最近 50 条均出现 A07089", "速度偏差 46.3%", "负载率 78.47%"],
            processing_steps=["备份参数", "检查单位制参数", "复核速度反馈链路"],
            acceptance_criteria=["A07089 不再复现", "速度偏差低于阈值"],
            assignee_role="电气维护人员",
            suggested_completion_window="24小时内",
            thread_id="thread_demo",
            trace_id="trace_demo",
            request_id="req_demo",
        )
    )

    record = response["work_order"]
    assert record["work_order_id"].startswith("WO-")
    assert record["status"] == "待派单"
    assert record["trace_id"] == "trace_demo"
    assert record["request_id"] == "req_demo"
    assert record["due_at"]

    listed = service.list_work_orders(trace_id="trace_demo")
    assert listed["summary"]["total"] == 1
    assert listed["items"][0]["work_order_id"] == record["work_order_id"]
