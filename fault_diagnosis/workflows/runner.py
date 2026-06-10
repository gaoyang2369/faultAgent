"""Workflow 公共入口。"""

from __future__ import annotations

from typing import AsyncGenerator

from .contracts import WorkflowType
from .router import route_workflow_request
from .scenarios.clarification import ClarificationRunner
from .scenarios.fault_diagnosis import FaultDiagnosisRunner, WorkflowExecutionError
from .scenarios.manual_qa import ManualQaRunner
from .scenarios.report_generation import ReportGenerationRunner
from .scenarios.status_inspection import StatusInspectionRunner

DiagnosisWorkflowRunner = FaultDiagnosisRunner


def _normalize_workflow_type(workflow_type: WorkflowType | str) -> str:
    """将路由结果统一为字符串，便于做 Runner 映射。"""

    if isinstance(workflow_type, WorkflowType):
        return workflow_type.value
    return str(workflow_type).strip()


def _get_runner_class(workflow_type: WorkflowType | str):
    """根据路由类型返回对应场景 Runner 类。"""

    workflow_key = _normalize_workflow_type(workflow_type)
    runner_class_by_workflow = {
        WorkflowType.FAULT_DIAGNOSIS.value: FaultDiagnosisRunner,
        WorkflowType.STATUS_INSPECTION.value: StatusInspectionRunner,
        WorkflowType.MANUAL_QA.value: ManualQaRunner,
        WorkflowType.REPORT_GENERATION.value: ReportGenerationRunner,
        WorkflowType.CLARIFICATION.value: ClarificationRunner,
    }
    return runner_class_by_workflow.get(workflow_key, FaultDiagnosisRunner)


def build_workflow_runner(message: str, thread_id: str, user_identity: str = "游客"):
    """基于路由结果构建对应场景 Runner。"""

    route_result = route_workflow_request(message, user_identity)
    runner_class = _get_runner_class(route_result.workflow_type)
    runner = runner_class(message=message, thread_id=thread_id, user_identity=user_identity)
    runner.route_result = route_result
    return runner, route_result


async def stream_workflow_events(
    app,
    message: str,
    thread_id: str,
    user_identity: str = "游客",
    request_id: str | None = None,
    stream_id: str | None = None,
    cancel_handle=None,
) -> AsyncGenerator[str, None]:
    """统一 Workflow 入口，先路由再交给对应场景 Runner 输出事件。"""

    runner, _route_result = build_workflow_runner(
        message=message,
        thread_id=thread_id,
        user_identity=user_identity,
    )
    async for chunk in runner.stream_events(
        app,
        request_id=request_id,
        stream_id=stream_id,
        cancel_handle=cancel_handle,
    ):
        yield chunk
