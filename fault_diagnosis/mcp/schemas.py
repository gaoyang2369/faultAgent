"""MCP 协议层结构化模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class McpToolName(str, Enum):
    """MCP tool 名称。"""

    DIAGNOSE_FAULT = "diagnose_fault"
    GET_EQUIPMENT_INFO = "get_equipment_info"
    QUERY_EQUIPMENT_METRICS = "query_equipment_metrics"
    QUERY_EVENT_HISTORY = "query_event_history"
    GENERATE_DIAGNOSIS_ARTIFACT = "generate_diagnosis_artifact"
    QUERY_EQUIPMENT_DATA = "query_equipment_data"
    RETRIEVE_FAULT_KNOWLEDGE = "retrieve_fault_knowledge"
    GENERATE_DIAGNOSIS_REPORT = "generate_diagnosis_report"
    LIST_EQUIPMENT = "list_equipment"
    GET_EQUIPMENT_STATUS = "get_equipment_status"
    GET_EQUIPMENT_SNAPSHOT = "get_equipment_snapshot"
    QUERY_METRIC_TREND = "query_metric_trend"
    ANALYZE_METRIC_TREND = "analyze_metric_trend"
    QUERY_FAULT_HISTORY = "query_fault_history"
    QUERY_ALARM_HISTORY = "query_alarm_history"
    SEARCH_FAULT_KNOWLEDGE = "search_fault_knowledge"
    EXPLAIN_FAULT_CODE = "explain_fault_code"
    GET_FAULT_CONTEXT = "get_fault_context"
    ANALYZE_FAULT = "analyze_fault"
    RANK_POSSIBLE_CAUSES = "rank_possible_causes"
    SUGGEST_FAULT_ACTIONS = "suggest_fault_actions"
    CREATE_WORK_ORDER_DRAFT = "create_work_order_draft"


class McpBaseModel(BaseModel):
    """MCP 通用模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )


class McpResourceReference(McpBaseModel):
    """对外返回的资源引用。"""

    uri: str = Field(description="资源唯一标识，例如 reports://thread/abc/markdown")
    name: str = Field(description="资源名称")
    media_type: str = Field(default="text/plain", description="资源媒体类型")
    description: str = Field(default="", description="资源说明")


class McpFindingItem(McpBaseModel):
    """结构化结论项。"""

    finding_id: str = Field(description="结论唯一标识")
    title: str = Field(description="结论标题")
    summary: str = Field(default="", description="结论摘要")
    severity: str = Field(default="medium", description="严重等级")
    confidence: str = Field(default="unknown", description="置信度")


class McpEvidenceItem(McpBaseModel):
    """结构化证据项。"""

    evidence_id: str = Field(description="证据唯一标识")
    source_type: str = Field(description="证据来源类型")
    title: str = Field(description="证据标题")
    summary: str = Field(default="", description="证据摘要")
    source_uri: str | None = Field(default=None, description="证据来源路径或 URI")


class McpTimelineEntry(McpBaseModel):
    """时间线条目。"""

    timestamp: str = Field(description="时间戳")
    stage: str = Field(description="阶段名称")
    message: str = Field(description="阶段说明")


class McpArtifactItem(McpBaseModel):
    """结构化产物条目。"""

    artifact_id: str = Field(description="产物唯一标识")
    artifact_type: str = Field(description="产物类型")
    name: str = Field(description="产物名称")
    uri: str | None = Field(default=None, description="产物访问 URI")
    summary: str = Field(default="", description="产物摘要")


class McpGovernanceInfo(McpBaseModel):
    """执行治理与观测信息。"""

    trace_id: str | None = Field(default=None, description="请求级追踪标识")
    run_id: str | None = Field(default=None, description="本次执行标识")
    status: str = Field(default="success", description="执行状态")
    streamable: bool = Field(default=False, description="是否支持流式")
    emitted_events: list[str] = Field(default_factory=list, description="已发出的事件类型")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加治理信息")


class McpBaseRequest(McpBaseModel):
    """首批 MCP 请求的公共字段。"""

    request_id: str | None = Field(default=None, description="可选请求标识")
    user_identity: str = Field(default="游客", description="调用方身份")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加上下文")


class McpToolResponse(McpBaseModel):
    """首批 MCP tool 的统一成功响应。"""

    tool_name: str = Field(description="tool 名称")
    status: Literal["success"] = Field(default="success", description="固定为 success")
    summary: str = Field(default="", description="结果摘要")
    findings: list[McpFindingItem] = Field(default_factory=list, description="关键结论列表")
    evidence: list[McpEvidenceItem] = Field(default_factory=list, description="证据列表")
    timeline: list[McpTimelineEntry] = Field(default_factory=list, description="执行时间线")
    artifacts: list[McpArtifactItem] = Field(default_factory=list, description="产物列表")
    resources: list[McpResourceReference] = Field(default_factory=list, description="资源引用列表")
    governance: McpGovernanceInfo = Field(
        default_factory=McpGovernanceInfo,
        description="治理与观测信息",
    )


class DiagnoseFaultRequest(McpBaseRequest):
    """故障诊断 tool 的请求结构。"""

    user_message: str = Field(description="用户原始诊断问题")
    equipment_id: str | None = Field(default=None, description="设备编号")
    equipment_name: str | None = Field(default=None, description="设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    symptoms: list[str] = Field(default_factory=list, description="故障症状列表")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    needs_report: bool = Field(default=True, description="是否需要报告")
    report_format: str = Field(default="markdown", description="报告格式")
    time_range: dict[str, str | None] = Field(default_factory=dict, description="诊断时间范围")
    include_ranked_causes: bool = Field(default=True, description="是否返回原因排序")
    analysis_depth: Literal["basic", "standard", "deep"] = Field(default="standard", description="诊断深度")


class DiagnoseFaultResponse(McpToolResponse):
    """故障诊断 tool 的响应结构。"""

    tool_name: Literal[McpToolName.DIAGNOSE_FAULT] = McpToolName.DIAGNOSE_FAULT
    diagnosis: str = Field(default="", description="诊断结论")
    confidence: str = Field(default="unknown", description="诊断置信度")
    risk_level: str = Field(default="unknown", description="风险等级")
    recommended_actions: list[str] = Field(default_factory=list, description="建议动作")
    root_causes: list[str] = Field(default_factory=list, description="可能根因")
    diagnosis_summary: str = Field(default="", description="诊断摘要")
    ranked_causes: list[dict[str, Any]] = Field(default_factory=list, description="原因排序")
    recommended_next_steps: list[str] = Field(default_factory=list, description="建议下一步")
    resource_refs: list[str] = Field(default_factory=list, description="资源 URI 引用")


class GetEquipmentInfoRequest(McpBaseRequest):
    """设备发现、状态和快照聚合查询 tool 的请求结构。"""

    query_type: Literal["list", "status", "snapshot"] = Field(description="查询类型")
    equipment_id: str | None = Field(default=None, description="设备编号或设备名称")
    keyword: str | None = Field(default=None, description="设备列表关键字")
    filters: dict[str, Any] = Field(default_factory=dict, description="列表查询过滤条件")
    metric_names: list[str] = Field(default_factory=list, description="快照关注指标")
    include_metrics_summary: bool = Field(default=True, description="快照是否包含指标摘要")
    stale_after_minutes: int = Field(default=30, ge=1, le=1440, description="状态过期判断分钟数")
    window_minutes: int = Field(default=30, ge=1, le=10080, description="快照时间窗口")
    limit: int = Field(default=100, ge=1, le=500, description="返回数量上限")


class GetEquipmentInfoResponse(McpToolResponse):
    """设备发现、状态和快照聚合查询 tool 的响应结构。"""

    tool_name: Literal[McpToolName.GET_EQUIPMENT_INFO] = McpToolName.GET_EQUIPMENT_INFO
    query_type: Literal["list", "status", "snapshot"] = Field(description="查询类型")
    equipments: list[dict[str, Any]] = Field(default_factory=list, description="设备列表")
    total_count: int = Field(default=0, description="返回结果数量")
    equipment_id: str | None = Field(default=None, description="设备编号")
    equipment_name: str = Field(default="", description="设备名称")
    status: dict[str, Any] = Field(default_factory=dict, description="设备状态")
    snapshot: dict[str, Any] = Field(default_factory=dict, description="设备快照")


class QueryEquipmentDataRequest(McpBaseRequest):
    """设备数据查询 tool 的请求结构。"""

    equipment_id: str = Field(description="设备编号")
    metric_names: list[str] = Field(default_factory=list, description="指标名列表")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    limit: int = Field(default=200, ge=1, le=5000, description="返回数据上限")
    include_summary: bool = Field(default=True, description="是否返回摘要")


class QueryEquipmentDataResponse(McpToolResponse):
    """设备数据查询 tool 的响应结构。"""

    tool_name: Literal[McpToolName.QUERY_EQUIPMENT_DATA] = McpToolName.QUERY_EQUIPMENT_DATA
    metrics: list[str] = Field(default_factory=list, description="返回指标名列表")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="数据行列表")
    sample_count: int = Field(default=0, description="返回样本数")


class QueryEquipmentMetricsRequest(McpBaseRequest):
    """设备指标与传感器数据聚合查询 tool 的请求结构。"""

    equipment_id: str = Field(description="设备编号或设备名称")
    metric_names: list[str] = Field(default_factory=list, description="指标名列表")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    metric_mode: Literal["raw", "series", "trend"] = Field(default="raw", description="指标查询模式")
    aggregation: Literal["none", "avg", "max", "min", "latest"] = Field(default="none", description="轻量聚合方式")
    limit: int = Field(default=200, ge=1, le=5000, description="返回样本上限")
    include_summary: bool = Field(default=True, description="是否返回摘要")


class QueryEquipmentMetricsResponse(McpToolResponse):
    """设备指标与传感器数据聚合查询 tool 的响应结构。"""

    tool_name: Literal[McpToolName.QUERY_EQUIPMENT_METRICS] = McpToolName.QUERY_EQUIPMENT_METRICS
    equipment_id: str = Field(description="设备编号")
    metric_mode: Literal["raw", "series", "trend"] = Field(description="指标查询模式")
    aggregation: Literal["none", "avg", "max", "min", "latest"] = Field(default="none", description="轻量聚合方式")
    metrics: list[str] = Field(default_factory=list, description="返回指标名列表")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="原始数据行")
    points: list[dict[str, Any]] = Field(default_factory=list, description="时间序列点")
    trend_summaries: list[dict[str, Any]] = Field(default_factory=list, description="趋势摘要")
    aggregation_result: dict[str, Any] = Field(default_factory=dict, description="聚合结果")
    sample_count: int = Field(default=0, description="返回样本数")


class McpKnowledgeItem(McpBaseModel):
    """知识检索结果项。"""

    knowledge_id: str = Field(description="知识项标识")
    title: str = Field(description="知识标题")
    snippet: str = Field(description="知识摘要")
    source_uri: str | None = Field(default=None, description="知识来源 URI")
    score: float | None = Field(default=None, description="检索得分")


class McpEquipmentItem(McpBaseModel):
    """设备发现结果项。"""

    equipment_id: str = Field(description="设备编号")
    equipment_name: str = Field(default="", description="设备名称")
    line_name: str = Field(default="", description="产线名称")
    latest_time: str | None = Field(default=None, description="最近数据时间")
    status: str = Field(default="unknown", description="设备状态")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加设备信息")


class McpMetricPoint(McpBaseModel):
    """指标趋势点。"""

    timestamp: str = Field(description="时间戳")
    metric_name: str = Field(description="指标名称")
    value: float | None = Field(default=None, description="指标值")


class McpHistoryItem(McpBaseModel):
    """历史事件项。"""

    event_time: str = Field(description="事件时间")
    equipment_id: str = Field(default="", description="设备编号")
    equipment_name: str = Field(default="", description="设备名称")
    code: str = Field(default="", description="事件编码")
    level: str = Field(default="unknown", description="事件等级")
    status: str = Field(default="", description="事件状态")
    message: str = Field(default="", description="事件说明")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加信息")


class RetrieveFaultKnowledgeRequest(McpBaseRequest):
    """知识检索 tool 的请求结构。"""

    query: str = Field(default="", description="检索查询")
    knowledge_type: Literal["diagnosis", "general", "fault_code"] = Field(default="diagnosis", description="知识类型")
    equipment_type: str | None = Field(default=None, description="设备类型")
    fault_code: str | None = Field(default=None, description="故障码")
    top_k: int = Field(default=5, ge=1, le=20, description="返回知识条数")
    filters: dict[str, Any] = Field(default_factory=dict, description="检索过滤条件")


class RetrieveFaultKnowledgeResponse(McpToolResponse):
    """知识检索 tool 的响应结构。"""

    tool_name: Literal[McpToolName.RETRIEVE_FAULT_KNOWLEDGE] = McpToolName.RETRIEVE_FAULT_KNOWLEDGE
    knowledge_items: list[McpKnowledgeItem] = Field(default_factory=list, description="知识项列表")
    total_hits: int = Field(default=0, description="命中总数")


class ListEquipmentRequest(McpBaseRequest):
    """设备发现 tool 的请求结构。"""

    keyword: str | None = Field(default=None, description="可选设备编号、名称或产线关键词")
    limit: int = Field(default=100, ge=1, le=500, description="返回设备上限")


class ListEquipmentResponse(McpToolResponse):
    """设备发现 tool 的响应结构。"""

    tool_name: Literal[McpToolName.LIST_EQUIPMENT] = McpToolName.LIST_EQUIPMENT
    equipments: list[McpEquipmentItem] = Field(default_factory=list, description="设备列表")
    total_count: int = Field(default=0, description="返回设备数量")


class GetEquipmentStatusRequest(McpBaseRequest):
    """设备状态 tool 的请求结构。"""

    equipment_id: str = Field(description="设备编号或设备名称")
    stale_after_minutes: int = Field(default=30, ge=1, le=1440, description="超过该分钟数视为数据过旧")


class GetEquipmentStatusResponse(McpToolResponse):
    """设备状态 tool 的响应结构。"""

    tool_name: Literal[McpToolName.GET_EQUIPMENT_STATUS] = McpToolName.GET_EQUIPMENT_STATUS
    equipment_id: str = Field(description="设备编号")
    equipment_name: str = Field(default="", description="设备名称")
    status: str = Field(default="unknown", description="状态判断")
    latest_time: str | None = Field(default=None, description="最近数据时间")
    alarm_status: str = Field(default="", description="最近告警状态")
    active_fault_code: str | None = Field(default=None, description="当前故障码")
    metrics: dict[str, Any] = Field(default_factory=dict, description="最近关键指标")


class GetEquipmentSnapshotRequest(McpBaseRequest):
    """设备快照 tool 的请求结构。"""

    equipment_id: str = Field(description="设备编号或设备名称")
    metric_names: list[str] = Field(default_factory=list, description="关键指标列表")
    window_minutes: int = Field(default=30, ge=1, le=10080, description="快照时间窗口")
    limit: int = Field(default=20, ge=1, le=500, description="返回样本上限")


class GetEquipmentSnapshotResponse(McpToolResponse):
    """设备快照 tool 的响应结构。"""

    tool_name: Literal[McpToolName.GET_EQUIPMENT_SNAPSHOT] = McpToolName.GET_EQUIPMENT_SNAPSHOT
    equipment_id: str = Field(description="设备编号")
    equipment_name: str = Field(default="", description="设备名称")
    metrics: dict[str, Any] = Field(default_factory=dict, description="最新指标值")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="快照样本")
    sample_count: int = Field(default=0, description="样本数量")


class QueryMetricTrendRequest(McpBaseRequest):
    """指标趋势查询 tool 的请求结构。"""

    equipment_id: str = Field(description="设备编号或设备名称")
    metric_names: list[str] = Field(default_factory=list, description="指标名列表")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    limit: int = Field(default=200, ge=1, le=5000, description="返回样本上限")


class QueryMetricTrendResponse(McpToolResponse):
    """指标趋势查询 tool 的响应结构。"""

    tool_name: Literal[McpToolName.QUERY_METRIC_TREND] = McpToolName.QUERY_METRIC_TREND
    equipment_id: str = Field(description="设备编号")
    metrics: list[str] = Field(default_factory=list, description="指标名列表")
    points: list[McpMetricPoint] = Field(default_factory=list, description="时间序列点")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="原始行")
    sample_count: int = Field(default=0, description="原始样本数量")


class AnalyzeMetricTrendRequest(QueryMetricTrendRequest):
    """指标趋势分析 tool 的请求结构。"""

    thresholds: dict[str, float] = Field(default_factory=dict, description="可选指标阈值")
    trend_data: list[dict[str, Any]] = Field(default_factory=list, description="调用方已查询的趋势数据")
    analysis_goal: Literal["anomaly", "degradation", "comparison", "summary"] = Field(default="summary", description="分析目标")


class AnalyzeMetricTrendResponse(McpToolResponse):
    """指标趋势分析 tool 的响应结构。"""

    tool_name: Literal[McpToolName.ANALYZE_METRIC_TREND] = McpToolName.ANALYZE_METRIC_TREND
    equipment_id: str = Field(description="设备编号")
    conclusion: str = Field(default="", description="趋势结论")
    trend_summaries: list[dict[str, Any]] = Field(default_factory=list, description="各指标趋势摘要")
    sample_count: int = Field(default=0, description="样本数量")


class QueryFaultHistoryRequest(McpBaseRequest):
    """故障历史查询 tool 的请求结构。"""

    equipment_id: str | None = Field(default=None, description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    limit: int = Field(default=50, ge=1, le=1000, description="返回记录上限")


class QueryFaultHistoryResponse(McpToolResponse):
    """故障历史查询 tool 的响应结构。"""

    tool_name: Literal[McpToolName.QUERY_FAULT_HISTORY] = McpToolName.QUERY_FAULT_HISTORY
    records: list[McpHistoryItem] = Field(default_factory=list, description="故障历史记录")
    total_count: int = Field(default=0, description="记录数量")


class QueryAlarmHistoryRequest(McpBaseRequest):
    """告警历史查询 tool 的请求结构。"""

    equipment_id: str | None = Field(default=None, description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    limit: int = Field(default=50, ge=1, le=1000, description="返回记录上限")


class QueryAlarmHistoryResponse(McpToolResponse):
    """告警历史查询 tool 的响应结构。"""

    tool_name: Literal[McpToolName.QUERY_ALARM_HISTORY] = McpToolName.QUERY_ALARM_HISTORY
    records: list[McpHistoryItem] = Field(default_factory=list, description="告警历史记录")
    total_count: int = Field(default=0, description="记录数量")


class QueryEventHistoryRequest(McpBaseRequest):
    """故障历史与告警历史聚合查询 tool 的请求结构。"""

    equipment_id: str | None = Field(default=None, description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    event_type: Literal["fault", "alarm", "all"] = Field(default="all", description="事件类型")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    severity: str | None = Field(default=None, description="严重等级")
    limit: int = Field(default=50, ge=1, le=1000, description="返回记录上限")


class QueryEventHistoryResponse(McpToolResponse):
    """故障历史与告警历史聚合查询 tool 的响应结构。"""

    tool_name: Literal[McpToolName.QUERY_EVENT_HISTORY] = McpToolName.QUERY_EVENT_HISTORY
    event_type: Literal["fault", "alarm", "all"] = Field(default="all", description="事件类型")
    records: list[McpHistoryItem] = Field(default_factory=list, description="历史事件记录")
    total_count: int = Field(default=0, description="记录数量")
    fault_count: int = Field(default=0, description="故障记录数量")
    alarm_count: int = Field(default=0, description="告警记录数量")


class SearchFaultKnowledgeRequest(RetrieveFaultKnowledgeRequest):
    """通用故障知识检索 tool 的请求结构。"""

    equipment_id: str | None = Field(default=None, description="设备编号")


class SearchFaultKnowledgeResponse(McpToolResponse):
    """通用故障知识检索 tool 的响应结构。"""

    tool_name: Literal[McpToolName.SEARCH_FAULT_KNOWLEDGE] = McpToolName.SEARCH_FAULT_KNOWLEDGE
    knowledge_items: list[McpKnowledgeItem] = Field(default_factory=list, description="知识项列表")
    total_hits: int = Field(default=0, description="命中总数")


class ExplainFaultCodeRequest(McpBaseRequest):
    """故障码解释 tool 的请求结构。"""

    fault_code: str = Field(description="故障码")
    equipment_type: str | None = Field(default=None, description="设备类型")
    top_k: int = Field(default=5, ge=1, le=20, description="补充知识条数")


class ExplainFaultCodeResponse(McpToolResponse):
    """故障码解释 tool 的响应结构。"""

    tool_name: Literal[McpToolName.EXPLAIN_FAULT_CODE] = McpToolName.EXPLAIN_FAULT_CODE
    fault_code: str = Field(description="故障码")
    meaning: str = Field(default="", description="故障含义")
    possible_causes: list[str] = Field(default_factory=list, description="可能原因")
    suggestions: list[str] = Field(default_factory=list, description="建议处理方式")
    knowledge_items: list[McpKnowledgeItem] = Field(default_factory=list, description="补充知识")


class GetFaultContextRequest(McpBaseRequest):
    """故障上下文聚合 tool 的请求结构。"""

    equipment_id: str = Field(description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    metric_names: list[str] = Field(default_factory=list, description="关注指标")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    top_k: int = Field(default=5, ge=1, le=20, description="知识条数")
    include: list[Literal["equipment", "metrics", "events", "knowledge", "diagnosis"]] = Field(
        default_factory=lambda: ["equipment", "metrics", "events", "knowledge"],
        description="需要聚合的上下文模块",
    )
    symptoms: list[str] = Field(default_factory=list, description="可选故障现象")


class GetFaultContextResponse(McpToolResponse):
    """故障上下文聚合 tool 的响应结构。"""

    tool_name: Literal[McpToolName.GET_FAULT_CONTEXT] = McpToolName.GET_FAULT_CONTEXT
    equipment_id: str = Field(description="设备编号")
    fault_code: str | None = Field(default=None, description="故障码")
    status: dict[str, Any] = Field(default_factory=dict, description="设备状态")
    snapshot: dict[str, Any] = Field(default_factory=dict, description="指标快照")
    trend_summary: list[dict[str, Any]] = Field(default_factory=list, description="趋势摘要")
    fault_history: list[McpHistoryItem] = Field(default_factory=list, description="故障历史")
    alarm_history: list[McpHistoryItem] = Field(default_factory=list, description="告警历史")
    knowledge_items: list[McpKnowledgeItem] = Field(default_factory=list, description="知识片段")


class GenerateDiagnosisReportRequest(McpBaseRequest):
    """报告生成 tool 的请求结构。"""

    thread_id: str | None = Field(default=None, description="线程标识")
    report_title: str | None = Field(default=None, description="报告标题")
    report_format: str = Field(default="markdown", description="报告格式")
    include_html: bool = Field(default=False, description="是否同步生成 HTML 报告")
    summary: str = Field(default="", description="报告摘要")
    finding_ids: list[str] = Field(default_factory=list, description="引用的结论标识")
    artifact_ids: list[str] = Field(default_factory=list, description="引用的产物标识")
    source_trace_id: str | None = Field(default=None, description="来源 trace 标识")


class GenerateDiagnosisReportResponse(McpToolResponse):
    """报告生成 tool 的响应结构。"""

    tool_name: Literal[McpToolName.GENERATE_DIAGNOSIS_REPORT] = McpToolName.GENERATE_DIAGNOSIS_REPORT
    report_title: str | None = Field(default=None, description="报告标题")
    report_format: str = Field(default="markdown", description="报告格式")
    report_resource: McpResourceReference | None = Field(default=None, description="主报告资源")
    html_resource: McpResourceReference | None = Field(default=None, description="HTML 报告资源")


class GenerateDiagnosisArtifactRequest(McpBaseRequest):
    """诊断报告、处置建议和工单草稿聚合生成 tool 的请求结构。"""

    artifact_type: Literal["report", "action_suggestion", "work_order_draft"] = Field(
        description="产物类型"
    )
    diagnosis_result: dict[str, Any] = Field(default_factory=dict, description="可选诊断结果")
    equipment_id: str | None = Field(default=None, description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    time_range: dict[str, str | None] = Field(default_factory=dict, description="诊断时间范围")
    format: Literal["markdown", "html", "json"] = Field(default="markdown", description="产物格式")
    audience: Literal["operator", "engineer", "manager"] = Field(default="engineer", description="产物受众")
    thread_id: str | None = Field(default=None, description="关联诊断线程")
    report_title: str | None = Field(default=None, description="报告标题")
    conclusion: str = Field(default="", description="当前判断")
    work_order_id: str | None = Field(default=None, description="工单编号或前缀")
    title: str | None = Field(default=None, description="工单标题")
    severity: str = Field(default="medium", description="严重等级")
    summary: str = Field(default="", description="产物摘要")
    assignee: str = Field(default="maintenance-team", description="执行人或组织")


class GenerateDiagnosisArtifactResponse(McpToolResponse):
    """诊断报告、处置建议和工单草稿聚合生成 tool 的响应结构。"""

    tool_name: Literal[McpToolName.GENERATE_DIAGNOSIS_ARTIFACT] = McpToolName.GENERATE_DIAGNOSIS_ARTIFACT
    artifact_type: Literal["report", "action_suggestion", "work_order_draft"] = Field(
        description="产物类型"
    )
    artifact: dict[str, Any] = Field(default_factory=dict, description="聚合产物")
    report_resource: McpResourceReference | None = Field(default=None, description="报告资源")
    html_resource: McpResourceReference | None = Field(default=None, description="HTML 报告资源")
    recommended_actions: list[str] = Field(default_factory=list, description="处置建议")
    work_order_draft: dict[str, Any] = Field(default_factory=dict, description="工单草稿")


class AnalyzeFaultRequest(McpBaseRequest):
    equipment_id: str = Field(description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    thread_id: str | None = Field(default=None, description="关联诊断线程")
    top_k: int = Field(default=5, ge=1, le=10, description="输出候选原因数量")


class AnalyzeFaultResponse(McpToolResponse):
    tool_name: Literal[McpToolName.ANALYZE_FAULT] = McpToolName.ANALYZE_FAULT
    equipment_id: str = Field(description="设备编号")
    fault_code: str | None = Field(default=None, description="故障码")
    conclusion: str = Field(default="", description="综合判断")
    cause_rankings: list[dict[str, Any]] = Field(default_factory=list, description="原因排序")


class RankPossibleCausesRequest(McpBaseRequest):
    equipment_id: str = Field(description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    thread_id: str | None = Field(default=None, description="关联诊断线程")
    candidate_causes: list[str] = Field(default_factory=list, description="候选原因列表")
    top_k: int = Field(default=5, ge=1, le=10, description="返回数量上限")


class RankPossibleCausesResponse(McpToolResponse):
    tool_name: Literal[McpToolName.RANK_POSSIBLE_CAUSES] = McpToolName.RANK_POSSIBLE_CAUSES
    equipment_id: str = Field(description="设备编号")
    fault_code: str | None = Field(default=None, description="故障码")
    ranked_causes: list[dict[str, Any]] = Field(default_factory=list, description="排序后的候选原因")


class SuggestFaultActionsRequest(McpBaseRequest):
    equipment_id: str = Field(description="设备编号或设备名称")
    fault_code: str | None = Field(default=None, description="故障码")
    conclusion: str = Field(default="", description="当前判断")
    top_k: int = Field(default=5, ge=1, le=10, description="返回建议数")


class SuggestFaultActionsResponse(McpToolResponse):
    tool_name: Literal[McpToolName.SUGGEST_FAULT_ACTIONS] = McpToolName.SUGGEST_FAULT_ACTIONS
    equipment_id: str = Field(description="设备编号")
    fault_code: str | None = Field(default=None, description="故障码")
    recommended_actions: list[str] = Field(default_factory=list, description="处置建议")
    work_order_hint: str = Field(default="", description="工单建议")


class CreateWorkOrderDraftRequest(McpBaseRequest):
    work_order_id: str = Field(description="工单编号或前缀")
    title: str = Field(description="工单标题")
    severity: str = Field(description="严重等级")
    summary: str = Field(description="工单摘要")
    assignee: str = Field(default="maintenance-team", description="执行人或组织")
    source_report: str = Field(default="", description="来源报告")


class CreateWorkOrderDraftResponse(McpToolResponse):
    tool_name: Literal[McpToolName.CREATE_WORK_ORDER_DRAFT] = McpToolName.CREATE_WORK_ORDER_DRAFT
    work_order_id: str = Field(description="工单编号或前缀")
    draft: dict[str, Any] = Field(default_factory=dict, description="工单草稿")
    publication_status: str = Field(default="draft", description="发布状态")


class McpProgressEvent(McpBaseModel):
    """协议层的结构化进度事件。"""

    event_type: Literal["tool_progress"] = "tool_progress"
    trace_id: str = Field(description="请求级追踪标识")
    run_id: str = Field(description="本次执行标识")
    tool_name: str = Field(description="tool 名称")
    stage: str = Field(description="当前阶段")
    message: str = Field(description="阶段说明")
    progress: float | None = Field(default=None, ge=0.0, le=1.0, description="阶段进度")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加信息")


class McpToolStreamEvent(McpBaseModel):
    """协议层的结构化流式事件。"""

    event_type: Literal["tool_stream"] = "tool_stream"
    trace_id: str = Field(description="请求级追踪标识")
    run_id: str = Field(description="本次执行标识")
    tool_name: str = Field(description="tool 名称")
    chunk: str = Field(default="", description="流式内容片段")
    done: bool = Field(default=False, description="是否已结束")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加信息")


class McpErrorPayload(McpBaseModel):
    """结构化错误负载。"""

    code: str = Field(description="错误码")
    message: str = Field(description="错误说明")
    retryable: bool = Field(default=False, description="是否可重试")
    details: dict[str, Any] = Field(default_factory=dict, description="错误细节")
    trace_id: str | None = Field(default=None, description="请求级追踪标识")
    run_id: str | None = Field(default=None, description="本次执行标识")


class McpErrorEvent(McpBaseModel):
    """协议层的结构化错误事件。"""

    event_type: Literal["server_error"] = "server_error"
    error: McpErrorPayload
