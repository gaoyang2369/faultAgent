"""线程级诊断产物合同。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiagnosisArtifactType(str, Enum):
    """线程级产物类型。

    除 `fault_diagnosis` 外的值主要用于兼容历史 artifact 读取。
    """

    FAULT_DIAGNOSIS = "fault_diagnosis"
    STATUS_QUERY = "status_query"
    ALARM_TRIAGE = "alarm_triage"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    HEALTH_ASSESSMENT = "health_assessment"
    KNOWLEDGE_QA = "knowledge_qa"
    ACTION_REQUEST = "action_request"
    STATUS_INSPECTION = "status_inspection"
    MANUAL_QA = "manual_qa"
    REPORT_GENERATION = "report_generation"
    CLARIFICATION = "clarification"
    PERMISSION_SCOPE_QUERY = "permission_scope_query"


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


QualityLevel = Literal["high", "medium", "low"]
FreshnessLevel = Literal["current", "recent", "stale", "unknown"]
CompletenessLevel = Literal["complete", "partial", "missing"]
ClaimStatus = Literal["candidate", "confirmed", "rejected", "final"]
WorkOrderLifecycleStatus = Literal[
    "not_evaluated",
    "not_recommended",
    "recommended_draft",
    "draft_created",
    "dispatch_forbidden",
    "dispatch_requires_external_approval",
]
WorkOrderDraftStatus = Literal["draft", "pending_verification"]


class EvidenceQuality(BaseModel):
    """证据质量标签，用于校验和最终披露。"""

    reliability: QualityLevel = Field(default="medium", description="来源可靠性")
    freshness: FreshnessLevel = Field(default="unknown", description="时效性")
    relevance: QualityLevel = Field(default="medium", description="与任务相关性")
    completeness: CompletenessLevel = Field(default="partial", description="完整性")


class ClaimConfidence(BaseModel):
    """Claim 置信度。"""

    level: QualityLevel = Field(default="medium", description="置信度等级")
    score: float | None = Field(default=None, ge=0.0, le=1.0, description="0-1 置信度分数")
    reason: str = Field(default="", description="置信度说明")


class EvidenceItem(BaseModel):
    """单条可追溯、可引用、可校验的事实证据。

    ``source_type/title/content/importance`` 保留为兼容字段，历史 artifact
    和前端标准化适配器仍可按旧合同读取。
    """

    model_config = ConfigDict(extra="allow")

    evidence_id: str = Field(default="", description="证据唯一标识")
    evidence_type: str = Field(default="generic", description="证据类型")
    source_type: str = Field(default="generic", description="证据来源类型")
    source_name: str = Field(default="", description="来源名称，如表名、知识库或工具名")
    asset_id: str | None = Field(default=None, description="设备或资产标识")
    asset_type: str | None = Field(default=None, description="设备或资产类型")
    timestamp: str | None = Field(default=None, description="证据对应的单点时间")
    time_range: dict[str, str] | None = Field(default=None, description="证据对应的时间窗口")
    content: Any = Field(default_factory=dict, description="结构化事实本体或兼容文本")
    summary: str = Field(default="", description="给 LLM、报告和前端使用的短摘要")
    quality: EvidenceQuality = Field(default_factory=EvidenceQuality, description="证据质量")
    metadata: dict[str, Any] = Field(default_factory=dict, description="来源追踪元数据")

    # Legacy display fields.
    title: str = Field(default="", description="兼容旧前端的证据标题")
    importance: str = Field(default="medium", description="兼容旧前端的证据重要性")

    @model_validator(mode="after")
    def _populate_display_fields(self) -> "EvidenceItem":
        if not self.summary:
            if isinstance(self.content, str) and self.content.strip():
                self.summary = self.content.strip()
            elif self.title:
                self.summary = self.title
        if not self.title:
            self.title = self.summary or self.evidence_type or "证据项"
        if not self.source_name:
            self.source_name = self.source_type
        return self


class Claim(BaseModel):
    """基于 EvidenceItem 形成的可校验判断。"""

    claim_id: str = Field(description="判断唯一标识")
    claim_type: str = Field(description="判断类型")
    asset_id: str | None = Field(default=None, description="关联设备或资产")
    statement: str = Field(description="判断陈述")
    confidence: ClaimConfidence = Field(default_factory=ClaimConfidence, description="置信度")
    supporting_evidence_ids: list[str] = Field(default_factory=list, description="支持证据 ID")
    contradicting_evidence_ids: list[str] = Field(default_factory=list, description="反证 ID")
    missing_evidence: list[str] = Field(default_factory=list, description="缺失证据")
    reasoning_summary: str = Field(default="", description="短推理摘要，不承载长链路思考")
    status: ClaimStatus = Field(default="candidate", description="判断状态")
    created_by: str = Field(default="agent_engine_v2", description="创建节点")
    decision: str | None = Field(default=None, description="决策类 Claim 的决策值")
    reason_codes: list[str] = Field(default_factory=list, description="规则或原因编码")


class EvidenceBundle(BaseModel):
    """一次任务的完整证据包。"""

    bundle_id: str = Field(description="证据包唯一标识")
    trace_id: str = Field(description="关联 trace_id")
    task: dict[str, Any] = Field(default_factory=dict, description="任务结构化描述")
    evidence_items: list[EvidenceItem] = Field(default_factory=list, description="事实证据")
    claims: list[Claim] = Field(default_factory=list, description="基于证据形成的判断")
    final_claim_ids: list[str] = Field(default_factory=list, description="最终采用的 Claim")
    quality_checks: dict[str, Any] = Field(default_factory=dict, description="证据链质量校验结果")
    artifacts: dict[str, Any] = Field(default_factory=dict, description="关联产物索引")


class SqlStepArtifact(BaseModel):
    """SQL 阶段产物。"""

    artifact_id: str = ""
    success: bool
    summary: str
    sql_used: list[str] = Field(default_factory=list)
    result_preview: str = ""
    raw_output: str = ""
    error: str | None = None
    access_scope: dict[str, Any] = Field(default_factory=dict)
    filters_applied: list[str] = Field(default_factory=list)
    row_count: int | None = None
    parse_status: str = ""
    source_table: str = ""
    data_state: str = "ok"
    query_status: Literal["success", "empty", "failed"] = "empty"
    data_basis: dict[str, Any] = Field(default_factory=dict)
    requested_window: dict[str, Any] = Field(default_factory=dict)
    resolved_window: dict[str, Any] = Field(default_factory=dict)
    latest_sample_time: str = ""
    sample_count: int = 0
    runtime_status: Literal["normal", "attention", "abnormal", "unknown"] = "unknown"
    status_reasons: list[str] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class FaultCodeEntry(BaseModel):
    """Structured manual entry extracted from knowledge chunks."""

    code: str = ""
    title: str = ""
    meaning: str = ""
    cause: str = ""
    remedy: str = ""
    category: str = ""
    drive_object: str = ""
    component: str = ""
    propagation: str = ""
    reaction: str = ""
    acknowledgement: str = ""
    references: list[str] = Field(default_factory=list)
    source_file: str = ""
    page: str = ""
    match_type: str = "candidate_match"


class KnowledgeStepArtifact(BaseModel):
    """知识检索阶段产物。"""

    artifact_id: str = ""
    success: bool
    query: str
    snippets: list[str] = Field(default_factory=list)
    raw_output: str = ""
    error: str | None = None
    error_code: str = ""
    evidence_usable: bool = True
    available: bool = True
    hit_count: int | None = None
    fault_codes: list[str] = Field(default_factory=list)
    fault_code_entries: list[FaultCodeEntry] = Field(default_factory=list)


class AnalysisStepArtifact(BaseModel):
    """分析阶段产物。"""

    artifact_id: str = ""
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

    lifecycle_status: WorkOrderLifecycleStatus = Field(default="not_recommended", description="工单生命周期状态")
    need_workorder: bool | None = Field(default=None, description="兼容字段：是否建议生成维修工单")
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
    source_diagnosis_artifact_id: str | None = Field(default=None, description="原始诊断 artifact id")
    source_report_artifact_id: str | None = Field(default=None, description="原始报告 artifact id")
    pending_action: dict[str, Any] | None = Field(default=None, description="待用户确认的后续动作")

    @model_validator(mode="after")
    def _populate_lifecycle_compat(self) -> "WorkOrderSuggestion":
        if self.lifecycle_status == "not_recommended" and self.need_workorder is True:
            self.lifecycle_status = "recommended_draft"
        if self.lifecycle_status == "not_evaluated":
            self.need_workorder = None
            if self.status == "待派单":
                self.status = "未评估"
            return self
        if self.lifecycle_status == "recommended_draft":
            self.need_workorder = True
            if self.status == "待派单":
                self.status = "待确认"
            return self
        if self.lifecycle_status == "not_recommended":
            self.need_workorder = False
        return self


class WorkOrderDraftArtifact(BaseModel):
    """Agent 生成的待人工确认工单草稿，不代表派发或执行。"""

    draft_id: str
    source_diagnosis_artifact_id: str
    source_report_artifact_id: str | None = None
    device: str
    fault_code: str | None = None
    priority: str = "P2"
    workorder_type: str = "运行异常确认工单"
    recommended_assignee_role: str = "电气维护人员"
    acceptance_criteria: list[str] = Field(default_factory=list)
    stale_warning: str | None = None
    status: WorkOrderDraftStatus = "draft"
    source_hash: str
    title: str = ""
    created_from_recommendation_artifact_id: str | None = None
    dispatch_policy: str = "dispatch_requires_external_approval"


class ReportStepArtifact(BaseModel):
    """报告阶段产物。"""

    artifact_id: str = ""
    success: bool
    report_filename: str | None = None
    report_title: str | None = None
    report_url: str | None = None
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
