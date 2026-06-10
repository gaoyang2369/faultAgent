"""MCP server registration and invocation helpers."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from .errors import McpErrorCode, McpProtocolError, coerce_protocol_error
from .resources import (
    read_diagnosis_evidence_summary,
    read_diagnosis_report_markdown,
    read_fault_knowledge_reference,
)
from .schemas import (
    AnalyzeMetricTrendRequest,
    AnalyzeMetricTrendResponse,
    DiagnoseFaultRequest,
    DiagnoseFaultResponse,
    GetEquipmentInfoRequest,
    GetEquipmentInfoResponse,
    GetFaultContextRequest,
    GetFaultContextResponse,
    GenerateDiagnosisArtifactRequest,
    GenerateDiagnosisArtifactResponse,
    McpErrorEvent,
    McpProgressEvent,
    McpToolName,
    McpToolResponse,
    McpToolStreamEvent,
    QueryEquipmentMetricsRequest,
    QueryEquipmentMetricsResponse,
    QueryEventHistoryRequest,
    QueryEventHistoryResponse,
    RetrieveFaultKnowledgeRequest,
    RetrieveFaultKnowledgeResponse,
)
from .tools import (
    analyze_metric_trend_handler,
    diagnose_fault_handler,
    generate_diagnosis_artifact_handler,
    get_equipment_info_handler,
    get_fault_context_handler,
    query_equipment_metrics_handler,
    query_event_history_handler,
    retrieve_fault_knowledge_handler,
)

ToolHandler = Callable[..., Any]
ResourceHandler = Callable[..., Any]


@dataclass(slots=True)
class McpInvocationContext:
    """Single MCP tool invocation context."""

    trace_id: str
    run_id: str
    tool_name: str
    requested_at: str
    user_identity: str = "游客"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class McpToolDefinition:
    """Registered MCP tool definition."""

    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler | None = None
    streamable: bool = False
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class McpResourceDefinition:
    """Registered MCP resource definition."""

    name: str
    title: str
    description: str
    handler: ResourceHandler | None = None
    media_type: str = "text/plain"
    tags: tuple[str, ...] = ()


class McpServerInfo(BaseModel):
    """Registered MCP server information."""

    name: str
    version: str
    tool_count: int
    resource_count: int
    registered_tools: list[str]
    registered_resources: list[str]


class McpServer:
    """Minimal MCP server container used by the project."""

    def __init__(self, *, name: str, version: str = "0.1.0") -> None:
        self.name = name
        self.version = version
        self._tools: dict[str, McpToolDefinition] = {}
        self._resources: dict[str, McpResourceDefinition] = {}

    def register_tool(self, definition: McpToolDefinition) -> None:
        self._tools[definition.name] = definition

    def register_resource(self, definition: McpResourceDefinition) -> None:
        self._resources[definition.name] = definition

    def get_tool(self, name: str) -> McpToolDefinition:
        definition = self._tools.get(name)
        if definition is None:
            raise McpProtocolError(
                code=McpErrorCode.TOOL_NOT_FOUND,
                message=f"未注册的 MCP tool: {name}",
                details={"tool_name": name},
            )
        return definition

    def get_resource(self, name: str) -> McpResourceDefinition:
        definition = self._resources.get(name)
        if definition is None:
            raise McpProtocolError(
                code=McpErrorCode.RESOURCE_NOT_FOUND,
                message=f"未注册的 MCP resource: {name}",
                details={"resource_name": name},
            )
        return definition

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def list_resources(self) -> list[str]:
        return list(self._resources.keys())

    def list_tool_definitions(self) -> list[McpToolDefinition]:
        return list(self._tools.values())

    def list_resource_definitions(self) -> list[McpResourceDefinition]:
        return list(self._resources.values())

    def build_info(self) -> McpServerInfo:
        return McpServerInfo(
            name=self.name,
            version=self.version,
            tool_count=len(self._tools),
            resource_count=len(self._resources),
            registered_tools=self.list_tools(),
            registered_resources=self.list_resources(),
        )

    def build_trace_id(self) -> str:
        return f"trace_{uuid4().hex[:12]}"

    def build_run_id(self, tool_name: str) -> str:
        return f"{tool_name}-{uuid4().hex[:10]}"

    def build_progress_event(
        self,
        *,
        trace_id: str,
        run_id: str,
        tool_name: str,
        stage: str,
        message: str,
        progress: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> McpProgressEvent:
        return McpProgressEvent(
            trace_id=trace_id,
            run_id=run_id,
            tool_name=tool_name,
            stage=stage,
            message=message,
            progress=progress,
            metadata=metadata or {},
        )

    def build_stream_event(
        self,
        *,
        trace_id: str,
        run_id: str,
        tool_name: str,
        chunk: str,
        done: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> McpToolStreamEvent:
        return McpToolStreamEvent(
            trace_id=trace_id,
            run_id=run_id,
            tool_name=tool_name,
            chunk=chunk,
            done=done,
            metadata=metadata or {},
        )

    def build_error_event(self, error: McpProtocolError) -> McpErrorEvent:
        return McpErrorEvent(error=error.to_payload())

    async def invoke_tool(
        self,
        name: str,
        payload: dict[str, Any] | BaseModel,
        *,
        trace_id: str | None = None,
        user_identity: str = "游客",
        metadata: dict[str, Any] | None = None,
    ) -> BaseModel:
        definition = self.get_tool(name)
        trace_id = trace_id or self.build_trace_id()
        run_id = self.build_run_id(name)
        context = McpInvocationContext(
            trace_id=trace_id,
            run_id=run_id,
            tool_name=name,
            requested_at=datetime.now().isoformat(),
            user_identity=user_identity,
            metadata=metadata or {},
        )

        try:
            request_model = self._coerce_request_model(
                definition=definition,
                payload=payload,
                trace_id=trace_id,
                run_id=run_id,
            )
            result = await self._invoke_handler(
                definition=definition,
                request_model=request_model,
                context=context,
            )
            return self._coerce_response_model(
                definition=definition,
                result=result,
                trace_id=trace_id,
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise coerce_protocol_error(exc, trace_id=trace_id, run_id=run_id) from exc

    async def read_resource(
        self,
        name: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        definition = self.get_resource(name)
        if definition.handler is None:
            raise McpProtocolError(
                code=McpErrorCode.NOT_IMPLEMENTED,
                message=f"MCP resource 尚未提供读取实现: {name}",
                details={"resource_name": name},
            )
        result = definition.handler(payload or {})
        if inspect.isawaitable(result):
            return await result
        return result

    def _coerce_request_model(
        self,
        *,
        definition: McpToolDefinition,
        payload: dict[str, Any] | BaseModel,
        trace_id: str,
        run_id: str,
    ) -> BaseModel:
        if isinstance(payload, definition.input_model):
            return payload
        raw_payload = payload.model_dump() if isinstance(payload, BaseModel) else payload
        try:
            return definition.input_model.model_validate(raw_payload)
        except ValidationError as exc:
            raise McpProtocolError.from_validation_error(exc, trace_id=trace_id, run_id=run_id) from exc

    async def _invoke_handler(
        self,
        *,
        definition: McpToolDefinition,
        request_model: BaseModel,
        context: McpInvocationContext,
    ) -> Any:
        if definition.handler is None:
            raise McpProtocolError(
                code=McpErrorCode.NOT_IMPLEMENTED,
                message=f"MCP tool 尚未提供业务实现: {definition.name}",
                details={"tool_name": definition.name},
                trace_id=context.trace_id,
                run_id=context.run_id,
            )

        signature = inspect.signature(definition.handler)
        parameter_count = len(signature.parameters)
        result = definition.handler(request_model, context) if parameter_count >= 2 else definition.handler(request_model)
        if inspect.isawaitable(result):
            return await result
        return result

    def _coerce_response_model(
        self,
        *,
        definition: McpToolDefinition,
        result: Any,
        trace_id: str,
        run_id: str,
    ) -> BaseModel:
        if isinstance(result, definition.output_model):
            response_model = result
        else:
            try:
                response_model = definition.output_model.model_validate(result)
            except ValidationError as exc:
                raise McpProtocolError.from_validation_error(exc, trace_id=trace_id, run_id=run_id) from exc

        if isinstance(response_model, McpToolResponse):
            governance = response_model.governance.model_copy(
                update={
                    "trace_id": trace_id,
                    "run_id": run_id,
                    "streamable": definition.streamable,
                }
            )
            response_model = response_model.model_copy(update={"governance": governance})
        return response_model


def _sectioned_description(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if "：" in text:
            title, body = text.split("：", 1)
            parts.append(title.strip())
            parts.append(body.strip())
        else:
            parts.append(text)
    return "\n".join(parts)


DIAGNOSE_FAULT_DESCRIPTION = _sectioned_description(
    [
        "用途：基于设备、故障码、症状和时间范围发起完整故障诊断，并沉淀可复用的诊断线程、结论、证据和报告资源。",
        "适用场景：用户明确要做完整诊断，或后续可能继续生成正式报告。",
        "前置条件：必须提供 user_message；建议提供 equipment_id、fault_code；若后续要生成报告，建议在 metadata.thread_id 中显式传固定线程标识。",
        "输入要点：metadata.thread_id 用于复用同一诊断线程；needs_report 和 report_format 用于控制报告产物。",
        "输出要点：返回 summary、diagnosis、findings、evidence、resources；governance 中包含 trace_id 和 run_id。",
        "推荐步骤：先收集设备编号、故障码、症状，再显式设置 metadata.thread_id 调用本工具；需要正式报告时，继续使用同一 thread_id 调用 generate_diagnosis_artifact，并设置 artifact_type=report。",
        "不要用于：只想拿轻量上下文或快速联调时，优先使用 get_fault_context。",
        "常见失败：缺少关键设备信息，或未显式传 thread_id 导致后续报告链路不稳定。",
    ]
)

GET_FAULT_CONTEXT_DESCRIPTION = _sectioned_description(
    [
        "用途：聚合设备状态、指标快照、趋势、历史和知识片段，快速形成轻量故障上下文。",
        "适用场景：初步排查、信息汇总、GUI 联调验证，或在完整诊断前先快速摸清现场。",
        "前置条件：必须提供 equipment_id；fault_code、metric_names、时间范围为可选增强信息。",
        "输入要点：equipment_id 决定主查询对象；fault_code 用于聚焦故障语境；metric_names 用于缩小关注指标。",
        "输出要点：返回 status、snapshot、trend_summary、fault_history、alarm_history、knowledge_items，并附带 evidence resource。",
        "推荐步骤：先调用本工具观察上下文，再根据需要继续调用 diagnose_fault。",
        "后续动作：可读取返回的 evidence resource 做联调留档；若要生成正式报告，应切换到 diagnose_fault 并显式传 metadata.thread_id。",
        "不要用于：不要把本工具产生的上下文结果直接当作可生成正式诊断报告的完成态线程。",
    ]
)

GENERATE_DIAGNOSIS_REPORT_DESCRIPTION = _sectioned_description(
    [
        "用途：基于已有诊断线程生成 markdown 或 HTML 诊断报告，并暴露 reports resource。",
        "适用场景：用户已经完成故障诊断，需要正式报告产物或可读取的报告资源。",
        "前置条件：必须提供 thread_id；该 thread_id 必须已存在结构化诊断产物；仅由 get_fault_context 产生的上下文线程通常不能直接用于正式报告生成。",
        "输入要点：thread_id 是核心；report_title 用于报告标题；report_format 默认为 markdown；include_html 用于控制是否同时生成 HTML。",
        "输出要点：返回 summary、report_resource、html_resource（若启用）、findings、evidence、resources 和 governance。",
        "推荐步骤：先调用 diagnose_fault，并显式传 metadata.thread_id；确认诊断完成后，使用同一个 thread_id 调用本工具；再读取 reports://thread/{thread_id}/markdown。",
        "后续动作：若报告生成失败，使用同一个 thread_id 调用 explain_report_gate 检查门禁原因。",
        "不要用于：不要在没有 thread_id 时调用；不要指望本工具替代 diagnose_fault 自动补齐诊断线程。",
        "常见失败：缺少 thread_id、当前线程没有结构化诊断产物，或线程门禁未通过。",
    ]
)

EXPLAIN_REPORT_GATE_DESCRIPTION = _sectioned_description(
    [
        "用途：用通俗语言解释当前线程为什么能出报告或不能出报告，并给出下一步建议。",
        "适用场景：报告生成被拦截，或在正式调用 generate_diagnosis_report 之前先检查门禁状态。",
        "前置条件：建议提供诊断线程 thread_id；若已有 report_gate 快照，也可结合当前上下文解释。",
        "输入要点：thread_id 用于定位同一诊断线程；report_gate 可用于补充已有门禁结果。",
        "输出要点：返回 report_gate、explanation、recommendation，并保留治理信息用于联调追踪。",
        "推荐步骤：先完成 diagnose_fault；若 generate_diagnosis_report 失败或用户追问为什么不能出报告，再用同一 thread_id 调用本工具。",
        "后续动作：根据 recommendation 补充证据、回到 diagnose_fault，或再次尝试 generate_diagnosis_report。",
        "不要用于：不要把本工具当成诊断工具或报告生成工具；它只负责解释门禁和建议下一步。",
        "常见失败：线程下缺少证据快照或结构化产物，导致只能解释为上下文不足。",
    ]
)

QUERY_EQUIPMENT_DATA_DESCRIPTION = _sectioned_description(
    [
        "用途：查询设备实时或历史指标数据，返回结构化数据行并生成可复用的证据摘要资源。",
        "适用场景：需要核对原始监测数据、做趋势分析前取数，或补充诊断证据。",
        "前置条件：必须提供 equipment_id；metric_names、时间范围和 limit 用于缩小查询范围。",
        "输入要点：metric_names 为空时会使用默认关键指标；start_time 和 end_time 用于限定时间窗口。",
        "输出要点：返回 metrics、rows、sample_count，并附带 evidence resource 和 SQL 查询摘要。",
        "推荐步骤：先确定设备编号，再按需设置指标和时间范围；若后续要做趋势判断，可继续调用 query_metric_trend 或 analyze_metric_trend。",
        "不要用于：不要把本工具当成完整诊断工具；它只负责取数和返回结构化样本。",
        "常见失败：设备编号不存在、指标名不受支持，或上游数据库不可用。",
    ]
)

RETRIEVE_FAULT_KNOWLEDGE_DESCRIPTION = _sectioned_description(
    [
        "用途：按查询词、故障码和过滤条件检索知识库，返回命中的知识片段和引用资源。",
        "适用场景：需要补充维修经验、故障码背景、可能原因或标准处理建议。",
        "前置条件：必须提供 query；可选提供 fault_code、equipment_type、top_k 和 filters。",
        "输入要点：query 决定检索语义；top_k 控制返回条数；filters 用于缩小知识范围。",
        "输出要点：返回 knowledge_items、total_hits，并提供 knowledge resource 便于后续读取明细。",
        "推荐步骤：先用自然语言组织故障问题；解释故障码时设置 knowledge_type=fault_code，完整诊断时继续调用 diagnose_fault。",
        "不要用于：不要把本工具当成设备实时状态查询工具；它不读取现场数据。",
        "常见失败：知识库未建立索引、查询词过空泛，或没有命中相关知识片段。",
    ]
)

LIST_EQUIPMENT_DESCRIPTION = _sectioned_description(
    [
        "用途：列出系统内可用设备及最近数据时间，帮助确认可诊断对象。",
        "适用场景：不知道设备编号、需要做设备发现，或在 GUI 客户端里先验证数据覆盖范围。",
        "前置条件：无需 thread_id；keyword 和 limit 为可选过滤条件。",
        "输入要点：keyword 可按设备编号、名称或产线筛选；limit 控制返回设备数量。",
        "输出要点：返回 equipments、total_count，每台设备包含 latest_time、status 和基础 metadata。",
        "推荐步骤：先用本工具找设备，再把选中的 equipment_id 交给 get_equipment_status、get_fault_context 或 diagnose_fault。",
        "不要用于：不要拿它做单设备深度诊断；它只负责发现和粗筛。",
        "常见失败：关键字过窄导致无结果，或实时数据表当前不可访问。",
    ]
)

GET_EQUIPMENT_STATUS_DESCRIPTION = _sectioned_description(
    [
        "用途：读取单台设备最近状态、告警状态和关键指标，给出当前健康判断。",
        "适用场景：想快速确认设备是否在线、是否告警、是否存在活动故障码。",
        "前置条件：必须提供 equipment_id；stale_after_minutes 用于定义数据过旧阈值。",
        "输入要点：equipment_id 可以是设备编号或设备名称；stale_after_minutes 影响 offline 判断。",
        "输出要点：返回 status、latest_time、alarm_status、active_fault_code 和最近关键指标。",
        "推荐步骤：先确认设备编号，再调用本工具获取现状；若状态异常，可继续调用 get_fault_context 或 diagnose_fault。",
        "不要用于：不要把它当成历史趋势分析工具；它只看最近一条状态。",
        "常见失败：设备不存在、最新数据缺失，或数据时间过旧导致状态被判为 offline。",
    ]
)

GET_EQUIPMENT_SNAPSHOT_DESCRIPTION = _sectioned_description(
    [
        "用途：读取设备最近一段时间的关键指标快照，帮助观察短窗口内的指标分布。",
        "适用场景：想看最近 30 分钟到数小时内的关键样本，而不是完整长趋势。",
        "前置条件：必须提供 equipment_id；window_minutes、metric_names 和 limit 为主要控制参数。",
        "输入要点：window_minutes 定义回看窗口；metric_names 控制快照中关注的指标集合。",
        "输出要点：返回最新 metrics、原始 rows 和 sample_count，可作为上下文输入给其他诊断工具。",
        "推荐步骤：先获取设备快照，再决定是否需要调用 query_metric_trend 或 get_fault_context 做更完整分析。",
        "不要用于：不要把它当成长时间趋势工具；它更适合短窗口观察。",
        "常见失败：窗口内没有样本、设备编号错误，或指标列不受支持。",
    ]
)

QUERY_METRIC_TREND_DESCRIPTION = _sectioned_description(
    [
        "用途：查询设备关键指标的时间序列数据，返回标准化趋势点和原始样本行。",
        "适用场景：需要观察负载、温度、振动等指标随时间的变化，并给后续分析工具供数。",
        "前置条件：必须提供 equipment_id；metric_names、时间范围和 limit 建议显式设置。",
        "输入要点：metric_names 为空时会使用默认指标；start_time、end_time 用于限定趋势区间。",
        "输出要点：返回 metrics、points、rows、sample_count，并附带查询得到的趋势数据。",
        "推荐步骤：先取趋势数据，再调用 analyze_metric_trend 做摘要，或在 diagnose_fault 中作为证据理解。",
        "不要用于：不要期望本工具直接给出诊断结论；它只返回趋势数据。",
        "常见失败：时间范围无数据、设备不存在，或 limit 设置过小导致观察不完整。",
    ]
)

ANALYZE_METRIC_TREND_DESCRIPTION = _sectioned_description(
    [
        "用途：基于时间序列数据给出轻量趋势结论和各指标摘要。",
        "适用场景：已经有趋势数据，需要快速判断哪些指标在上升、越限或异常。",
        "前置条件：必须提供 equipment_id；建议同时提供 metric_names 和阈值 thresholds。",
        "输入要点：thresholds 用于控制越限判断；若未提供，会使用默认阈值。",
        "输出要点：返回 conclusion、trend_summaries 和 sample_count，适合直接给用户或喂给后续诊断工具。",
        "推荐步骤：先确保查询窗口合理，再调用本工具；若需要更完整结论，可继续走 get_fault_context 或 diagnose_fault。",
        "不要用于：不要把它当成正式故障结论工具；它只做轻量趋势摘要。",
        "常见失败：样本不足、指标值缺失，或阈值设置与现场实际不匹配。",
    ]
)

QUERY_FAULT_HISTORY_DESCRIPTION = _sectioned_description(
    [
        "用途：查询设备或故障码的历史故障记录，帮助回看相似故障和重复发生情况。",
        "适用场景：需要确认 F01002 是否高频出现、是否有相同设备反复故障，或补充诊断时间线。",
        "前置条件：建议至少提供 equipment_id 或 fault_code；时间范围和 limit 为可选控制条件。",
        "输入要点：equipment_id 和 fault_code 可联合过滤；start_time、end_time 用于限定历史区间。",
        "输出要点：返回 records 和 total_count，每条记录包含事件时间、等级、说明和关联 metadata。",
        "推荐步骤：先按设备或故障码过滤，再结合告警历史和知识检索理解故障脉络。",
        "不要用于：不要把它当成实时状态工具；它关注历史记录。",
        "常见失败：过滤条件过严导致无结果，或上游表缺少对应历史记录。",
    ]
)

QUERY_ALARM_HISTORY_DESCRIPTION = _sectioned_description(
    [
        "用途：查询设备告警历史和告警脉络，补充当前故障前后的报警背景。",
        "适用场景：需要看告警是否先于故障出现、是否被确认或清除，以及告警密度变化。",
        "前置条件：建议至少提供 equipment_id 或 fault_code；时间范围和 limit 为可选控制条件。",
        "输入要点：fault_code 可用于聚焦特定告警；时间范围用于定位事件窗口。",
        "输出要点：返回 records 和 total_count，每条记录包含告警状态、等级、说明及附加信息。",
        "推荐步骤：结合 query_fault_history 一起看，可更完整地还原异常前后过程。",
        "不要用于：不要把它当成设备发现或实时监测工具；它只查历史告警。",
        "常见失败：设备无告警历史、时间窗口过窄，或 fault_code 过滤过严。",
    ]
)

SEARCH_FAULT_KNOWLEDGE_DESCRIPTION = _sectioned_description(
    [
        "用途：按设备、故障码和关键词检索知识库，返回更面向现场场景的故障知识命中。",
        "适用场景：已经知道设备和故障码，想快速看相应知识片段，而不是泛化检索。",
        "前置条件：建议提供 equipment_id 或 fault_code；keyword 可用于增强语义匹配。",
        "输入要点：fault_code 用于聚焦故障语境；keyword 用于补充现场现象；top_k 控制返回数量。",
        "输出要点：返回 knowledge_items、total_hits，并提供可复用的知识结果。",
        "推荐步骤：先缩小故障范围，再读知识片段；若需要解释具体故障码，可继续调用 explain_fault_code。",
        "不要用于：不要把它当成实时数据查询工具；它只从知识库取内容。",
        "常见失败：关键词不明确、知识库没有对应条目，或输入信息过少导致命中弱。",
    ]
)

EXPLAIN_FAULT_CODE_DESCRIPTION = _sectioned_description(
    [
        "用途：解释故障码含义、常见原因和建议处理方式，并补充关联知识片段。",
        "适用场景：用户明确询问某个故障码是什么意思，或需要快速拿到标准解释。",
        "前置条件：必须提供 fault_code；equipment_type 和 top_k 为可选增强参数。",
        "输入要点：fault_code 是核心；equipment_type 可帮助缩小设备背景；top_k 控制补充知识条数。",
        "输出要点：返回 meaning、possible_causes、suggestions 和 knowledge_items。",
        "推荐步骤：先解释故障码，再决定是否需要调用 get_fault_context 或 diagnose_fault 结合现场数据做判断。",
        "不要用于：不要把它当成完整诊断结论；它偏标准解释和知识补充。",
        "常见失败：故障码不存在、知识库没有对应说明，或设备类型不匹配。",
    ]
)

ANALYZE_FAULT_DESCRIPTION = _sectioned_description(
    [
        "用途：基于现有诊断上下文给出初步判断、原因排序和门禁摘要。",
        "适用场景：已经有 thread_id 或证据快照，想在不重跑完整诊断的情况下做进一步判断。",
        "前置条件：建议提供 equipment_id；若要复用现有上下文，建议提供 thread_id 或 findings/evidence 快照。",
        "输入要点：thread_id 用于读取已沉淀的证据上下文；fault_code 和 conclusion 可用于增强判断焦点。",
        "输出要点：返回 conclusion、cause_rankings、report_gate 和 evidence_quality。",
        "推荐步骤：先用 get_fault_context 或 diagnose_fault 形成上下文，再调用本工具做结构化判断。",
        "不要用于：不要把它当成原始取数工具；它依赖已有上下文，而不是直接采集现场数据。",
        "常见失败：缺少 thread_id 或可用证据快照，导致只能基于很弱的上下文做判断。",
    ]
)

RANK_POSSIBLE_CAUSES_DESCRIPTION = _sectioned_description(
    [
        "用途：对多个候选原因依据当前证据重新排序，帮助快速缩小排查范围。",
        "适用场景：已有几个怀疑原因，但需要结合当前证据判断哪个更优先。",
        "前置条件：必须提供 equipment_id；建议提供 thread_id 或 candidate_causes。",
        "输入要点：candidate_causes 是排序对象；thread_id 用于复用诊断线程内的证据摘要。",
        "输出要点：返回 ranked_causes，包含候选原因、得分和排序依据。",
        "推荐步骤：先通过 get_fault_context、analyze_fault 或经验列出候选原因，再调用本工具重排优先级。",
        "不要用于：不要在没有候选原因也没有上下文的情况下调用；否则排序价值有限。",
        "常见失败：候选原因为空、thread_id 缺失，或上下文证据不足以形成有区分度的排序。",
    ]
)

EVALUATE_EVIDENCE_QUALITY_DESCRIPTION = _sectioned_description(
    [
        "用途：评估当前结论与证据之间的覆盖度、质量门禁和可出报告性。",
        "适用场景：需要判断当前诊断是否足够支撑正式报告、工单或后续自动化动作。",
        "前置条件：建议提供 thread_id；若没有线程，也可显式传 findings、links 和 evidence 快照。",
        "输入要点：findings_snapshot、finding_links_snapshot、evidence_records_snapshot 共同决定质量评分和门禁结论。",
        "输出要点：返回 evidence_quality 和 report_gate，适合作为 explain_report_gate 或 create_work_order_draft 的上游输入。",
        "推荐步骤：先形成初步结论，再用本工具做质量复核；若门禁未通过，可补证据后再次评估。",
        "不要用于：不要把它当成生成结论的工具；它评估的是结论质量，不是结论本身。",
        "常见失败：证据为空、结论和证据没有绑定关系，或输入快照结构不完整。",
    ]
)

SUGGEST_FAULT_ACTIONS_DESCRIPTION = _sectioned_description(
    [
        "用途：根据当前判断和门禁状态输出下一步处置建议与工单提示。",
        "适用场景：已有初步判断，想知道下一步应检查什么、先停机还是继续观察。",
        "前置条件：建议提供 equipment_id、fault_code 和 conclusion；report_gate 可提升建议精度。",
        "输入要点：conclusion 决定建议方向；report_gate 决定建议是保守推进还是可以直接执行。",
        "输出要点：返回 recommended_actions 和 work_order_hint，便于转给维修或操作团队。",
        "推荐步骤：先完成 analyze_fault 或 evaluate_evidence_quality，再调用本工具生成执行建议。",
        "不要用于：不要把它当成正式工单生成器；若要结构化工单草稿，应继续调用 create_work_order_draft。",
        "常见失败：结论过空、门禁未知，导致建议只能停留在泛化层面。",
    ]
)

CREATE_WORK_ORDER_DRAFT_DESCRIPTION = _sectioned_description(
    [
        "用途：基于诊断结论和门禁状态生成结构化工单草稿，便于人工复核和后续派发。",
        "适用场景：已经有较明确的故障结论，希望形成标准化维修工单草稿。",
        "前置条件：必须提供 work_order_id；建议同时提供 equipment_id、fault_code、conclusion 和 report_gate。",
        "输入要点：report_gate 决定工单草稿的稳妥程度；severity、summary 等内容会反映当前结论强弱。",
        "输出要点：返回 draft、publication_status 和工单核心字段，适合人工确认后入正式系统。",
        "推荐步骤：先完成 analyze_fault 或 suggest_fault_actions，再调用本工具沉淀工单草稿。",
        "不要用于：不要在门禁明显未通过时直接作为正式工单发布依据；它只是草稿。",
        "常见失败：缺少必要结论字段、门禁状态不足，或输入信息无法支撑可执行工单。",
    ]
)

GET_EQUIPMENT_INFO_DESCRIPTION = _sectioned_description(
    [
        "用途：统一查询设备列表、设备状态和设备快照。",
        "适用场景：外部 Agent 需要发现设备、确认设备当前状态或读取设备概览。",
        "输入要点：query_type 可选 list、status、snapshot；status 和 snapshot 必须提供 equipment_id。",
        "输出要点：返回 equipments、status 或 snapshot，并保留底层 SQL 证据和治理信息。",
    ]
)

QUERY_EQUIPMENT_METRICS_DESCRIPTION = _sectioned_description(
    [
        "用途：统一查询设备指标原始数据、时间序列和轻量趋势数据。",
        "适用场景：需要原始传感器数据、历史序列或趋势点时调用。",
        "输入要点：metric_mode 可选 raw、series、trend；aggregation 可选 none、avg、max、min、latest。",
        "输出要点：返回 rows、points、trend_summaries 和 aggregation_result。",
    ]
)

QUERY_EVENT_HISTORY_DESCRIPTION = _sectioned_description(
    [
        "用途：统一查询历史故障和历史告警。",
        "适用场景：需要回溯设备事件、故障码记录或告警脉络时调用。",
        "输入要点：event_type 可选 fault、alarm、all，可按设备、故障码、时间范围和严重等级过滤。",
        "输出要点：返回 records、fault_count、alarm_count，并保留底层证据信息。",
    ]
)

GENERATE_DIAGNOSIS_ARTIFACT_DESCRIPTION = _sectioned_description(
    [
        "用途：统一生成诊断报告、报告门禁解释、处置建议和工单草稿。",
        "适用场景：用户需要可交付材料或后续处置材料时调用。",
        "输入要点：artifact_type 可选 report、gate_explanation、action_suggestion、work_order_draft。",
        "输出要点：返回 artifact、report_resource、recommended_actions 或 work_order_draft。",
    ]
)


TOOL_DESCRIPTION_OVERRIDES = {
    McpToolName.DIAGNOSE_FAULT.value: DIAGNOSE_FAULT_DESCRIPTION,
    McpToolName.GET_EQUIPMENT_INFO.value: GET_EQUIPMENT_INFO_DESCRIPTION,
    McpToolName.QUERY_EQUIPMENT_METRICS.value: QUERY_EQUIPMENT_METRICS_DESCRIPTION,
    McpToolName.QUERY_EVENT_HISTORY.value: QUERY_EVENT_HISTORY_DESCRIPTION,
    McpToolName.GENERATE_DIAGNOSIS_ARTIFACT.value: GENERATE_DIAGNOSIS_ARTIFACT_DESCRIPTION,
    McpToolName.RETRIEVE_FAULT_KNOWLEDGE.value: RETRIEVE_FAULT_KNOWLEDGE_DESCRIPTION,
    McpToolName.ANALYZE_METRIC_TREND.value: ANALYZE_METRIC_TREND_DESCRIPTION,
    McpToolName.GET_FAULT_CONTEXT.value: GET_FAULT_CONTEXT_DESCRIPTION,
}

CONSOLIDATED_TOOL_NAMES = {
    McpToolName.DIAGNOSE_FAULT.value,
    McpToolName.GET_EQUIPMENT_INFO.value,
    McpToolName.QUERY_EQUIPMENT_METRICS.value,
    McpToolName.ANALYZE_METRIC_TREND.value,
    McpToolName.QUERY_EVENT_HISTORY.value,
    McpToolName.RETRIEVE_FAULT_KNOWLEDGE.value,
    McpToolName.GET_FAULT_CONTEXT.value,
    McpToolName.GENERATE_DIAGNOSIS_ARTIFACT.value,
}

def build_fault_diagnosis_mcp_server() -> McpServer:
    """Construct the first-batch MCP server with real handlers attached."""

    server = McpServer(name="fault-diagnosis-mcp", version="0.1.0")
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.DIAGNOSE_FAULT.value,
            title="故障诊断",
            description="统一对外暴露故障诊断能力。",
            input_model=DiagnoseFaultRequest,
            output_model=DiagnoseFaultResponse,
            handler=diagnose_fault_handler,
            streamable=True,
            tags=("diagnosis", "phase1"),
        )
    )
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.GET_EQUIPMENT_INFO.value,
            title="设备信息聚合查询",
            description="统一查询设备列表、状态和快照。",
            input_model=GetEquipmentInfoRequest,
            output_model=GetEquipmentInfoResponse,
            handler=get_equipment_info_handler,
            tags=("equipment", "aggregate", "phase-consolidation"),
        )
    )
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.QUERY_EQUIPMENT_METRICS.value,
            title="设备指标聚合查询",
            description="统一查询设备指标原始数据、序列和趋势。",
            input_model=QueryEquipmentMetricsRequest,
            output_model=QueryEquipmentMetricsResponse,
            handler=query_equipment_metrics_handler,
            tags=("metric", "aggregate", "phase-consolidation"),
        )
    )
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.RETRIEVE_FAULT_KNOWLEDGE.value,
            title="知识检索",
            description="统一对外暴露故障知识检索能力。",
            input_model=RetrieveFaultKnowledgeRequest,
            output_model=RetrieveFaultKnowledgeResponse,
            handler=retrieve_fault_knowledge_handler,
            streamable=False,
            tags=("knowledge", "phase1"),
        )
    )
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.GENERATE_DIAGNOSIS_ARTIFACT.value,
            title="诊断产物聚合生成",
            description="统一生成报告、门禁解释、处置建议和工单草稿。",
            input_model=GenerateDiagnosisArtifactRequest,
            output_model=GenerateDiagnosisArtifactResponse,
            handler=generate_diagnosis_artifact_handler,
            tags=("artifact", "aggregate", "phase-consolidation"),
        )
    )
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.ANALYZE_METRIC_TREND.value,
            title="指标趋势分析",
            description="基于时间序列数据给出轻量趋势摘要。",
            input_model=AnalyzeMetricTrendRequest,
            output_model=AnalyzeMetricTrendResponse,
            handler=analyze_metric_trend_handler,
            tags=("metric", "trend", "phase7"),
        )
    )
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.QUERY_EVENT_HISTORY.value,
            title="事件历史聚合查询",
            description="统一查询故障历史和告警历史。",
            input_model=QueryEventHistoryRequest,
            output_model=QueryEventHistoryResponse,
            handler=query_event_history_handler,
            tags=("history", "aggregate", "phase-consolidation"),
        )
    )
    server.register_tool(
        McpToolDefinition(
            name=McpToolName.GET_FAULT_CONTEXT.value,
            title="故障上下文",
            description="聚合设备状态、快照、趋势、历史和知识片段。",
            input_model=GetFaultContextRequest,
            output_model=GetFaultContextResponse,
            handler=get_fault_context_handler,
            tags=("context", "phase7"),
        )
    )

    server.register_resource(
        McpResourceDefinition(
            name="diagnosis_report_markdown",
            title="诊断报告 Markdown",
            description="读取诊断报告 Markdown 内容。",
            handler=read_diagnosis_report_markdown,
            media_type="text/markdown",
            tags=("report", "phase1"),
        )
    )
    server.register_resource(
        McpResourceDefinition(
            name="fault_knowledge_reference",
            title="知识引用明细",
            description="读取知识检索命中明细。",
            handler=read_fault_knowledge_reference,
            media_type="application/json",
            tags=("knowledge", "phase1"),
        )
    )
    server.register_resource(
        McpResourceDefinition(
            name="diagnosis_evidence_summary",
            title="诊断证据摘要",
            description="读取证据、门禁和结论摘要。",
            handler=read_diagnosis_evidence_summary,
            media_type="application/json",
            tags=("evidence", "phase1"),
        )
    )
    for tool_name, description in TOOL_DESCRIPTION_OVERRIDES.items():
        server.get_tool(tool_name).description = description
    return server
