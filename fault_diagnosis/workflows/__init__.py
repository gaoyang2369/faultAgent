"""Workflow 执行入口。"""

__all__ = [
    "ClarificationRunner",
    "DiagnosisWorkflowRunner",
    "FaultDiagnosisRunner",
    "ManualQaRunner",
    "ReportGenerationRunner",
    "StatusInspectionRunner",
    "WorkflowExecutionError",
    "stream_workflow_events",
]


def __getattr__(name):
    """按需加载运行器，避免导入 contracts 时提前拉起外部依赖。"""

    if name in __all__:
        from .runner import DiagnosisWorkflowRunner, WorkflowExecutionError, stream_workflow_events
        from .scenarios.clarification import ClarificationRunner
        from .scenarios.fault_diagnosis import FaultDiagnosisRunner
        from .scenarios.manual_qa import ManualQaRunner
        from .scenarios.report_generation import ReportGenerationRunner
        from .scenarios.status_inspection import StatusInspectionRunner

        exports = {
            "ClarificationRunner": ClarificationRunner,
            "DiagnosisWorkflowRunner": DiagnosisWorkflowRunner,
            "FaultDiagnosisRunner": FaultDiagnosisRunner,
            "ManualQaRunner": ManualQaRunner,
            "ReportGenerationRunner": ReportGenerationRunner,
            "StatusInspectionRunner": StatusInspectionRunner,
            "WorkflowExecutionError": WorkflowExecutionError,
            "stream_workflow_events": stream_workflow_events,
        }
        return exports[name]
    raise AttributeError(name)
