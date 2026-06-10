"""Workflow 边界规范。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .contracts import WorkflowType


class WorkflowBoundarySpec(BaseModel):
    """定义单条 Workflow 的目标、依赖能力与边界约束。"""

    model_config = ConfigDict(use_enum_values=True)

    workflow_type: WorkflowType
    display_name: str = Field(description="场景显示名称")
    goal: str = Field(description="场景目标")
    required_capabilities: list[str] = Field(default_factory=list, description="必需能力")
    required_slots: list[str] = Field(default_factory=list, description="必需输入槽位")
    optional_slots: list[str] = Field(default_factory=list, description="可选输入槽位")
    default_needs_report: bool = Field(default=False, description="默认是否需要报告")
    supports_artifact_resume: bool = Field(default=False, description="是否支持从上游 artifact 恢复")
    supports_evidence_gate: bool = Field(default=False, description="是否支持证据门禁")
    fallback_workflow: str | None = Field(default=None, description="当前流失败时建议回退的场景")
    review_workflow: str | None = Field(default=None, description="当前流建议进入的复核场景")


BOUNDARY_SPECS: dict[str, WorkflowBoundarySpec] = {
    WorkflowType.FAULT_DIAGNOSIS.value: WorkflowBoundarySpec(
        workflow_type=WorkflowType.FAULT_DIAGNOSIS,
        display_name="故障诊断流",
        goal="解释设备故障、异常原因与处置建议，并在需要时生成诊断报告。",
        required_capabilities=["sql", "knowledge_base", "final_answer"],
        required_slots=["analysis_goal"],
        optional_slots=["equipment_hint", "fault_code_hint", "metric_hint", "time_range_hint"],
        default_needs_report=True,
        supports_artifact_resume=False,
        supports_evidence_gate=True,
        fallback_workflow=WorkflowType.MANUAL_QA.value,
        review_workflow=WorkflowType.EVIDENCE_REVIEW.value,
    ),
    WorkflowType.STATUS_INSPECTION.value: WorkflowBoundarySpec(
        workflow_type=WorkflowType.STATUS_INSPECTION,
        display_name="状态巡检流",
        goal="概览设备运行状态、风险信号和建议动作，必要时生成巡检报告。",
        required_capabilities=["sql", "final_answer"],
        required_slots=["analysis_goal"],
        optional_slots=["equipment_hint", "metric_hint", "time_range_hint"],
        default_needs_report=False,
        supports_artifact_resume=False,
        supports_evidence_gate=True,
        fallback_workflow=WorkflowType.MANUAL_QA.value,
        review_workflow=WorkflowType.EVIDENCE_REVIEW.value,
    ),
    WorkflowType.MANUAL_QA.value: WorkflowBoundarySpec(
        workflow_type=WorkflowType.MANUAL_QA,
        display_name="手册问答流",
        goal="回答故障码释义、操作说明、手册知识和安全注意事项类问题。",
        required_capabilities=["knowledge_base", "final_answer"],
        required_slots=["analysis_goal"],
        optional_slots=["equipment_hint", "fault_code_hint", "metric_hint"],
        default_needs_report=False,
        supports_artifact_resume=False,
        supports_evidence_gate=False,
        fallback_workflow=None,
        review_workflow=None,
    ),
    WorkflowType.REPORT_GENERATION.value: WorkflowBoundarySpec(
        workflow_type=WorkflowType.REPORT_GENERATION,
        display_name="报告生成流",
        goal="消费当前线程已有的结构化产物，独立生成报告，不重复执行分析链路。",
        required_capabilities=["artifact_store", "report_builder"],
        required_slots=["analysis_goal"],
        optional_slots=[],
        default_needs_report=True,
        supports_artifact_resume=True,
        supports_evidence_gate=False,
        fallback_workflow=None,
        review_workflow=WorkflowType.EVIDENCE_REVIEW.value,
    ),
    WorkflowType.CLARIFICATION.value: WorkflowBoundarySpec(
        workflow_type=WorkflowType.CLARIFICATION,
        display_name="澄清流",
        goal="识别当前请求缺失的关键信息，并向用户索取最小必要补充信息。",
        required_capabilities=["request_parsing", "clarification_prompt"],
        required_slots=["analysis_goal"],
        optional_slots=["equipment_hint", "fault_code_hint", "metric_hint", "time_range_hint"],
        default_needs_report=False,
        supports_artifact_resume=False,
        supports_evidence_gate=False,
        fallback_workflow=WorkflowType.MANUAL_QA.value,
        review_workflow=None,
    ),
    WorkflowType.EVIDENCE_REVIEW.value: WorkflowBoundarySpec(
        workflow_type=WorkflowType.EVIDENCE_REVIEW,
        display_name="证据链复核流",
        goal="复核当前线程结论与证据之间的覆盖率、质量门禁和后续建议动作。",
        required_capabilities=["artifact_store", "evidence_registry", "final_answer"],
        required_slots=["analysis_goal"],
        optional_slots=["upstream_artifact", "evidence_records"],
        default_needs_report=False,
        supports_artifact_resume=True,
        supports_evidence_gate=True,
        fallback_workflow=WorkflowType.MANUAL_QA.value,
        review_workflow=None,
    ),
}


def get_workflow_boundary_spec(workflow_type: WorkflowType | str) -> WorkflowBoundarySpec:
    """读取指定 Workflow 的边界规范。"""

    workflow_key = workflow_type.value if isinstance(workflow_type, WorkflowType) else str(workflow_type).strip()
    spec = BOUNDARY_SPECS.get(workflow_key)
    if spec is None:
        raise KeyError(f"Unknown workflow boundary spec: {workflow_key}")
    return spec


def list_workflow_boundary_specs() -> list[WorkflowBoundarySpec]:
    """按定义顺序返回全部边界规范。"""

    return list(BOUNDARY_SPECS.values())
