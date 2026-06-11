"""线程级诊断产物合同。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DiagnosisArtifactType(str, Enum):
    """线程级产物类型。

    除 `fault_diagnosis` 外的值主要用于兼容历史 artifact 读取。
    """

    FAULT_DIAGNOSIS = "fault_diagnosis"
    STATUS_INSPECTION = "status_inspection"
    MANUAL_QA = "manual_qa"
    REPORT_GENERATION = "report_generation"
    CLARIFICATION = "clarification"


class DiagnosisRequest(BaseModel):
    """用户请求的结构化表达。"""

    user_message: str = Field(description="用户原始问题")
    user_identity: str = Field(description="用户身份")
    equipment_hint: str | None = Field(default=None, description="设备提示")
    metric_hint: str | None = Field(default=None, description="指标提示")
    fault_code_hint: str | None = Field(default=None, description="故障码提示")
    time_range_hint: str | None = Field(default=None, description="时间范围提示")
    needs_report: bool = Field(default=True, description="是否需要生成报告")
    report_format: str = Field(default="markdown", description="报告格式")
    analysis_goal: str = Field(description="分析目标")


class EvidenceItem(BaseModel):
    """结构化证据项。"""

    source_type: str = Field(description="证据来源类型")
    title: str = Field(description="证据标题")
    content: str = Field(description="证据内容")
    importance: str = Field(default="medium", description="证据重要性")


class SqlStepArtifact(BaseModel):
    """SQL 阶段产物。"""

    success: bool
    summary: str
    sql_used: list[str] = Field(default_factory=list)
    result_preview: str = ""
    raw_output: str = ""
    error: str | None = None


class KnowledgeStepArtifact(BaseModel):
    """知识检索阶段产物。"""

    success: bool
    query: str
    snippets: list[str] = Field(default_factory=list)
    raw_output: str = ""
    error: str | None = None


class AnalysisStepArtifact(BaseModel):
    """分析阶段产物。"""

    success: bool
    conclusion: str
    basis: list[str] = Field(default_factory=list)
    probable_causes: list[str] = Field(default_factory=list)
    verification_items: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risk_notice: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    confidence_details: list[str] = Field(default_factory=list)
    confidence: str = "low"
    error: str | None = None


class WorkOrderSuggestion(BaseModel):
    """工单建议阶段产物。"""

    need_workorder: bool = Field(default=False, description="是否建议生成维修工单")
    reason: str = Field(default="", description="建议或不建议生成工单的规则依据")
    workorder_type: str = Field(default="", description="建议工单类型")
    priority: str = Field(default="P2", description="建议优先级编码")
    priority_label: str = Field(default="", description="建议优先级说明")
    risk_level: str = Field(default="低", description="风险等级")
    assignee_role: str = Field(default="", description="建议负责人角色")
    suggested_completion_window: str = Field(default="", description="建议完成时限")
    diagnosis_conclusion: str = Field(default="", description="可写入工单的诊断结论")
    key_evidence: list[str] = Field(default_factory=list, description="可验收的关键证据")
    processing_steps: list[str] = Field(default_factory=list, description="任务化处理步骤")
    acceptance_criteria: list[str] = Field(default_factory=list, description="验收标准")
    task_mappings: list[dict[str, Any]] = Field(default_factory=list, description="诊断证据到工单任务的映射")
    equipment_object: str = Field(default="", description="设备对象")
    fault_code: str | None = Field(default=None, description="关联故障码或事件码")
    title: str = Field(default="", description="建议工单标题")
    trigger_source: str = Field(default="故障诊断 Agent", description="触发来源")
    status: str = Field(default="待派单", description="建议初始状态")


class ReportStepArtifact(BaseModel):
    """报告阶段产物。"""

    success: bool
    report_filename: str | None = None
    save_result: str = ""
    error: str | None = None


class DiagnosisArtifactEnvelope(BaseModel):
    """统一的线程级结构化产物容器。"""

    model_config = ConfigDict(use_enum_values=True)

    # Keep the persisted field name for compatibility with existing frontend
    # contracts and stored artifacts.
    workflow_type: DiagnosisArtifactType
    thread_id: str
    created_at: str
    request_summary: str
    final_answer: str
    report_filename: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
