"""Workflow 结构化合同。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowType(str, Enum):
    """Workflow 场景类型。"""

    FAULT_DIAGNOSIS = "fault_diagnosis"
    STATUS_INSPECTION = "status_inspection"
    MANUAL_QA = "manual_qa"
    REPORT_GENERATION = "report_generation"
    CLARIFICATION = "clarification"
    EVIDENCE_REVIEW = "evidence_review"


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


class WorkflowRouteResult(BaseModel):
    """Workflow 路由结果。"""

    model_config = ConfigDict(use_enum_values=True)

    workflow_type: WorkflowType
    confidence: str = Field(default="low", description="路由置信度")
    reason: str = Field(default="", description="路由原因")
    needs_sql: bool = Field(default=False, description="是否需要 SQL")
    needs_knowledge: bool = Field(default=False, description="是否需要知识库")
    needs_report: bool = Field(default=False, description="是否需要报告")
    candidate_workflows: list[str] = Field(default_factory=list, description="候选场景流")
    missing_slots: list[str] = Field(default_factory=list, description="当前识别出的缺失槽位")
    disambiguation_needed: bool = Field(default=False, description="是否需要先做澄清")
    review_needed: bool = Field(default=False, description="是否需要先做证据复核")
    upstream_artifact_required: bool = Field(default=False, description="是否依赖上游 artifact")


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
    recommendations: list[str] = Field(default_factory=list)
    risk_notice: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    confidence: str = "low"
    error: str | None = None


class ReportStepArtifact(BaseModel):
    """报告阶段产物。"""

    success: bool
    report_filename: str | None = None
    save_result: str = ""
    error: str | None = None


class InspectionStepArtifact(BaseModel):
    """状态巡检阶段产物。"""

    success: bool
    summary: str
    observed_metrics: list[str] = Field(default_factory=list)
    detected_anomalies: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    suggested_actions: list[str] = Field(default_factory=list)
    confidence: str = "low"
    error: str | None = None


class ManualQaArtifact(BaseModel):
    """手册问答阶段产物。"""

    success: bool
    question_type: str
    knowledge_query: str
    snippets: list[str] = Field(default_factory=list)
    answer: str
    missing_information: list[str] = Field(default_factory=list)
    confidence: str = "low"
    error: str | None = None


class ClarificationArtifact(BaseModel):
    """澄清阶段产物。"""

    success: bool
    candidate_workflows: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: str = "low"
    suggested_next_workflow: str | None = None
    error: str | None = None


class EvidenceReviewArtifact(BaseModel):
    """证据链复核阶段产物。"""

    success: bool
    review_target_workflow: str
    total_findings: int = 0
    total_evidences: int = 0
    coverage_score: float | None = None
    quality_gate_status: str = "unknown"
    unsupported_findings: list[str] = Field(default_factory=list)
    missing_evidence_ids: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    review_summary: str = ""
    error: str | None = None


class PlanningEvidenceRequirement(BaseModel):
    """planner 生成的证据需求。"""

    evidence_type: str = Field(description="证据类型，例如 sql / knowledge_base / artifact")
    description: str = Field(description="证据需求说明")
    required: bool = Field(default=True, description="是否为必需证据")
    source_hint: str = Field(default="", description="建议证据来源")
    missing_impact: str = Field(default="", description="缺失该证据时的影响")


class PlanningConstraint(BaseModel):
    """planner 生成的执行约束。"""

    name: str = Field(description="约束名称")
    description: str = Field(description="约束说明")
    severity: str = Field(default="warning", description="约束等级：info / warning / blocking")


class PlanningArtifact(BaseModel):
    """Workflow 执行前的结构化计划产物。"""

    model_config = ConfigDict(use_enum_values=True)

    success: bool = Field(default=True, description="计划是否生成成功")
    task_summary: str = Field(description="本次任务摘要")
    workflow_type: WorkflowType = Field(description="所属 Workflow 类型")
    diagnosis_goals: list[str] = Field(default_factory=list, description="本次需要覆盖的目标")
    required_evidence: list[PlanningEvidenceRequirement] = Field(default_factory=list, description="证据需求")
    constraints: list[PlanningConstraint] = Field(default_factory=list, description="执行约束")
    risk_flags: list[str] = Field(default_factory=list, description="风险与不确定性标记")
    clarification_questions: list[str] = Field(default_factory=list, description="需要澄清的问题")
    success_criteria: list[str] = Field(default_factory=list, description="成功标准")
    confidence: str = Field(default="medium", description="计划置信度：high / medium / low")
    fallback_used: bool = Field(default=False, description="是否使用规则 fallback")
    error: str | None = Field(default=None, description="fallback 或失败原因")


class WorkflowArtifactEnvelope(BaseModel):
    """统一的线程级结构化产物容器。"""

    model_config = ConfigDict(use_enum_values=True)

    workflow_type: WorkflowType
    thread_id: str
    created_at: str
    request_summary: str
    final_answer: str
    report_filename: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class WorkflowStepResult(BaseModel):
    """单步执行结果。"""

    step_name: str
    status: str
    summary: str
    error: str | None = None
    started_at: str
    finished_at: str


class WorkflowRunResult(BaseModel):
    """整条工作流执行结果。"""

    final_answer: str
    steps: list[WorkflowStepResult] = Field(default_factory=list)
    request: DiagnosisRequest
    sql_artifact: SqlStepArtifact | None = None
    knowledge_artifact: KnowledgeStepArtifact | None = None
    analysis_artifact: AnalysisStepArtifact | None = None
    inspection_artifact: InspectionStepArtifact | None = None
    manual_qa_artifact: ManualQaArtifact | None = None
    clarification_artifact: ClarificationArtifact | None = None
    evidence_review_artifact: EvidenceReviewArtifact | None = None
    report_artifact: ReportStepArtifact | None = None
    planning_artifact: PlanningArtifact | None = None
    route_result: WorkflowRouteResult | None = None
    todos: list = Field(default_factory=list)
