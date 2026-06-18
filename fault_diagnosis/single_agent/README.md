# single_agent 单 Agent 说明

`fault_diagnosis/single_agent/` 是故障诊断后端的 Agent 核心实现。它不是多 Agent 编排，也不是让模型自由循环选择工具的开放式 Agent，而是一条受限、可审计、固定阶段的单 Agent 诊断流水线：

```text
请求理解 -> 任务分类与 workflow policy -> 证据账本初始化
  -> 按 policy 收集运行数据/知识库/权限风险证据
  -> 诊断分析 -> 工单建议/报告
  -> 证据链校验 -> 最终回答 -> 输出校验 -> 保存诊断产物
```

这份 README 重点解释 Agent 层内部：流程、任务分类、workflow、工具调用、证据链和扩展约定。后端整体入口、服务层、SSE 契约和部署方式见上一级 [README](../README.md)。

## 入口与边界

外部请求不会直接调用阶段函数，而是走后端聊天流入口：

```text
GET /chat/stream
  -> fault_diagnosis/api/chat.py
  -> ChatService.stream_chat
  -> agent_runtime.streaming.token_stream_events
  -> RestrictedSingleAgentRunner.stream_events
```

语音兼容入口 `POST /agent/chat` 也复用同一条 SSE 流，只是在服务层把流式事件聚合成 JSON。

Agent 层只负责“如何完成一次诊断任务”，不负责 HTTP 权限、会话归属、历史索引、应用启动和数据库池初始化。这些属于 `api/`、`services/`、`auth/`、`repositories/`、`infrastructure/` 等后端层。

## 核心目录

```text
single_agent/
  __init__.py                 对外导出 RestrictedSingleAgentRunner 等公共类型
  contracts.py                单 Agent 限制、决策结果、trace 事件合同
  runner.py                   runner 门面、模型调用、工具白名单、trace、SSE 工具事件
  flow.py                     顶层流式状态机和阶段编排顺序
  stages.py                   understand/sql/knowledge/analysis/report/final/save 等阶段实现
  intent.py                   轻量问候、规则 fallback、能力决策
  workflow/
    contracts.py              TaskType、TaskRoute、WorkflowPolicy、WorkflowPlan
    router.py                 规则优先的任务分类、对象抽取、子目标拆解
    policies.py               每类任务的 workflow policy、节点开关、工具映射
    nodes.py                  权限检查、风险检查、处置建议、审计节点
    todos.py                  将内部阶段投影成前端 5 个 workflow 进度分组
  evidence/
    __init__.py               EvidenceBundle 门面与工具 evidence 预览
    sql.py                    从 SQL 结果构造运行数据证据
    knowledge.py              从知识库结果构造手册/故障码证据
    claims.py                 从分析结论和工单建议构造 Claim
    quality.py                证据链质量检查与输出 guardrail
  output/
    payloads.py               complete 事件 payload 构建与前端兼容字段合并
  support/
    json_utils.py             模型 JSON 抽取、修复、解析
    serialization.py          trace/tool 输出序列化和预览
    tool_access.py            知识库和报告工具懒加载
  artifacts.py                线程级诊断产物 envelope 构建
  final_answer.py             最终回答 fallback 模板
  prompts.py                  理解、分析、证据合成 prompt
  reporting.py                报告 payload、图表 payload、分析摘要兼容入口
  report_sections.py          报告文本章节构建
  reporting_defs.py           报告结构定义
  sql_safety.py               SQL schema、只读校验、fallback 查询
  sql_result_parser.py        SQL 工具输出解析
  workorder_suggestions.py    诊断产物到工单草稿建议
```

## 运行模型

### 受限单 Agent

`RestrictedSingleAgentRunner` 是当前唯一 Agent runner。它的关键约束在 `contracts.py`：

```python
class SingleAgentLimits(BaseModel):
    max_rounds: int = 16
    max_tool_calls: int = 4
    allowed_tools: tuple[str, ...] = (
        "sql_db_query_checker",
        "sql_db_query",
        "query_knowledge_base",
        "save_report",
    )
```

含义：

- `max_rounds` 限制一次运行最多进入多少个内部阶段。完整诊断链路包含证据初始化、校验、保存等阶段，所以当前默认是 16。
- `max_tool_calls` 限制工具调用次数。常规完整链路通常是 SQL checker、SQL query、知识库、报告，最多 4 次。
- `allowed_tools` 是兜底硬白名单。实际运行时还会被 workflow policy 解析出的 `runtime_tools` 收窄。

模型不会直接决定“调用哪个工具”。模型只参与请求理解、SQL 规划、诊断证据合成等文本/JSON 生成；工具由阶段代码按 policy 显式调用。

### 直接回复快路径

`intent.py` 会识别纯问候、能力询问和感谢，例如“你好”“你能做什么”“谢谢”。这类请求不进入 SQL、知识库或诊断链路，直接生成 `final_answer` 并发送 `complete`。

### 报告续写快路径

当用户说“基于刚才结果生成报告”“导出报告”等，并且当前 thread 已有保存过的诊断 artifact，`understand_request()` 会设置：

```text
report_from_previous_artifact = true
primary_task_type = report_generation
```

随后流程跳过重新查询 SQL/知识库，调用 `stream_report_from_previous_artifact()` 从线程级 artifact 映射报告输入并生成 HTML 报告。

## 任务分类

任务分类由三步组成：

1. `understand_request()` 先用规则 fallback 或模型理解，生成 `DiagnosisRequest`。
2. `workflow/router.py` 的 `route_task()` 根据关键词、设备、故障码、时间窗口、报告/动作意图等信息生成 `TaskRoute`。
3. `workflow/policies.py` 的 `build_workflow_plan()` 选择 `WorkflowPolicy`，解析启用节点和运行时工具白名单。

当前顶层任务类型在 `workflow/contracts.py` 的 `TaskType` 中定义：

| 任务类型 | 典型用户问题 | workflow 重点 | 默认输出 |
| --- | --- | --- | --- |
| `status_query` | “当前状态怎么样”“是否在线”“最近运行情况” | 运行数据查询、当前状态摘要、必要时工单判断 | 简短状态回答 |
| `alarm_triage` | “F01002 是什么”“这个告警还在吗”“严重吗” | 知识库解释、当前告警状态、处置建议 | 告警分诊回答 |
| `fault_diagnosis` | “为什么故障”“帮我诊断异常”“设备高温原因” | SQL + 知识库 + 诊断分析 + 处置建议 + 工单建议 | 诊断回答，可选报告 |
| `root_cause_analysis` | “做 RCA”“根因分析”“复盘” | 事件窗口、因果证据、影响范围、报告 | RCA 回答或报告 |
| `health_assessment` | “健康评分”“风险趋势”“是否劣化” | 趋势窗口、数据充分性、风险/预测边界 | 健康评估回答 |
| `knowledge_qa` | “故障码含义”“SOP 步骤”“手册怎么说” | 知识库证据、适用范围、安全提示 | 知识问答 |
| `report_generation` | “生成报告”“导出报告”“总结成文档” | 使用已有或新收集证据生成报告 | 报告链接和摘要 |
| `action_request` | “重启设备”“关闭告警”“派发工单” | 权限检查、风险检查、只给草稿/审批提示、审计 | 不直接执行动作 |

任务分类结果会写入 `SingleAgentDecision`，核心字段包括：

- `primary_task_type`：顶层任务类型。
- `route_confidence`：路由置信度。
- `objects`：设备、告警码、系统、位置、指标、主题。
- `time_window`：时间窗口或默认策略。
- `subgoals`：拆解后的子目标，可能是 `ready` 或 `blocked`。
- `missing_slots`：缺失槽位，例如 `device_id_or_system`、`time_window`。
- `risk_level`：`read_only`、`requires_confirmation`、`write_action`、`high_risk`。
- `requested_output`：`answer`、`report` 或 `action_confirmation`。
- `workflow_policy`：选中的 policy 全量配置。
- `enabled_nodes`：解析后的节点开关。
- `runtime_tools`：本轮实际允许调用的工具名。
- `guardrails`：本轮要遵守的输出和动作边界。

## Workflow Policy

每类任务都有一条 policy，定义：

- `required_slots`：完成任务最好具备的槽位。
- `conditional_required_slots`：某些节点启用时额外需要的槽位。
- `enabled_nodes`：节点是固定启用、固定禁用，还是按条件启用。
- `evidence_requirements`：证据链要求。
- `output_schema`：预期输出形态。
- `on_missing_evidence`：证据不足时的处理策略。
- `guardrails`：输出和动作安全边界。

注意：policy 里的 `allowed_tools`/`forbidden_tools` 是领域能力语义，例如“允许读资产库”“禁止设备控制写操作”。当前真正能被 runner 调用的工具只有 `runtime_tools` 与 `SingleAgentLimits.allowed_tools` 的交集。

节点解析逻辑在 `_resolve_node()`：

- `sql`：设备上下文、任务类型和 `need_sql` flag 决定是否查询数据库。
- `knowledge`：知识问答、告警码、处置需求会触发知识库。
- `resolution_recommendation`：需要处置建议时启用。
- `workorder_decision`：用户有工单意图且有设备上下文时启用。
- `report`：用户请求报告或输出形态为 report 时启用。
- `permission_check`、`risk_check`、`audit_log`：动作请求启用。

## 阶段流

正常诊断请求的主流程由 `flow.py` 驱动：

```text
start
  -> understand
  -> select_workflow_policy
  -> initialize_evidence_bundle
  -> permission_check              按 policy 可选
  -> risk_check                    按 policy 可选
  -> sql                           启用则执行，未启用则生成 skipped artifact
  -> knowledge                     启用则执行；若 SQL 结果发现故障码，也可补充触发
  -> analysis
  -> resolution_recommendation     按 policy 可选
  -> workorder_decision            启用则判断，否则生成 skipped suggestion
  -> report                        启用则生成 HTML，否则生成 skipped report artifact
  -> evidence_validation
  -> final_answer
  -> output_guardrail
  -> audit_log                     动作请求可选
  -> save_artifact
  -> token
  -> complete
```

阶段职责：

| 阶段 | 主要文件 | 产物 |
| --- | --- | --- |
| `understand` | `stages.py`、`intent.py` | `DiagnosisRequest`、`SingleAgentDecision` 初稿 |
| `select_workflow_policy` | `flow.py`、`workflow/policies.py` | `workflow_route`、`workflow_policy` artifact，设置 `runtime_tools` |
| `initialize_evidence_bundle` | `evidence/__init__.py` | 空的 `EvidenceBundle` 账本 |
| `permission_check` | `workflow/nodes.py` | 动作请求权限边界，默认不允许直接写操作 |
| `risk_check` | `workflow/nodes.py` | 动作请求风险等级和人工确认要求 |
| `sql` | `stages.py`、`sql_safety.py` | `SqlStepArtifact`，后续转 SQL evidence |
| `knowledge` | `stages.py`、`tools/kb_tools.py` | `KnowledgeStepArtifact`，后续转知识库 evidence |
| `analysis` | `stages.py`、`reporting.py`、`prompts.py` | `AnalysisStepArtifact` |
| `resolution_recommendation` | `workflow/nodes.py` | 处置建议节点产物 |
| `workorder_decision` | `workorder_suggestions.py` | `WorkOrderSuggestion` |
| `report` | `stages.py`、`tools/report_tools.py` | `ReportStepArtifact` 和 HTML 文件 |
| `evidence_validation` | `evidence/quality.py` | 完整 `EvidenceBundle` 与质量检查 |
| `final_answer` | `final_answer.py`、`stages.py` | 用户可读最终回答 |
| `output_guardrail` | `evidence/quality.py` | 输出与证据一致性检查 |
| `audit_log` | `workflow/nodes.py` | 动作请求审计信息 |
| `save_artifact` | `artifacts.py`、`diagnosis/artifact_store.py` | 线程级 `DiagnosisArtifactEnvelope` |

前端进度不直接展示全部内部阶段，而是由 `workflow/todos.py` 投影成 5 个分组：

```text
理解与规划 -> 收集证据 -> 诊断分析 -> 生成报告 -> 校验并完成
```

## 工具与调用方式

工具调用统一走 `RestrictedSingleAgentRunner._invoke_restricted_tool()`：

```text
_start_tool_call()
  -> 检查工具是否在本轮 runtime_tools 或兜底 allowed_tools 中
  -> 检查 max_tool_calls
  -> 写 trace tool_call
  -> 发送 SSE tool_start
invoke_tool()
  -> 支持 LangChain tool.ainvoke / tool.invoke / 普通 callable
_finish_tool_call()
  -> 写 trace tool_result
  -> 构造 tool_end
  -> 对 SQL/知识库工具补充 evidence preview
```

### 当前工具清单

| 工具名 | 实现位置 | 调用阶段 | 输入 | 输出与用途 | 关键限制 |
| --- | --- | --- | --- | --- | --- |
| `sql_db_query_checker` | `tools/sql_tools.py` 通过 `SQLDatabaseToolkit` 生成 | `sql` | `{"query": sql_query}` | 返回修正后的 SQL 文本 | fast plan 会跳过 checker；返回 SQL 仍必须只读且表名合法 |
| `sql_db_query` | `tools/sql_tools.py` 通过 `SQLDatabaseToolkit` 生成 | `sql` | `{"query": sql_query}` | 返回数据库查询结果，写入 `SqlStepArtifact` | 只能执行阶段生成并校验过的只读查询 |
| `query_knowledge_base` | `tools/kb_tools.py` | `knowledge` | `{"query": query}` | 返回故障码/手册/SOP 片段，写入 `KnowledgeStepArtifact` | 优先本地 PDF 故障码精确匹配，再查基础/上传 PDF 知识库 |
| `save_report` | `tools/report_tools.py` | `report` | `SaveReportSchema` 字段 | 生成 HTML 报告，返回 `/reports/*.html` 或失败信息 | 文件名会安全归一化，只允许写入报告目录 |

### SQL 工具安全

SQL 规划在 `sql_safety.py` 中集中处理：

- 只允许 `SELECT` 或 `WITH`。
- 只允许访问：
  - `real_data_01`
  - `real_data_02`
  - `real_data_03`
  - `device_alarm`
  - `device_metric`
  - `device_fault_data`
  - `fault_records`
- 禁止使用旧表 `real_data`。
- 当前/最近运行数据默认查 `real_data_01`。
- 设备过滤使用 `device_name` 或 `inverter_name`，不要假设 `real_data_01/02/03` 有 `device_id`。
- 如果模型生成 SQL 为空、非只读或包含未知表，会回退到 `build_fallback_sql_query()`。
- 常见运行状态/报告类请求会走 `build_fast_sql_plan()`，直接生成确定性 SQL 并跳过 checker。

### 知识库工具行为

`query_knowledge_base` 的检索顺序：

1. 从 query 中提取故障码。
2. 在本地 PDF 文本中做故障码精确匹配。
3. 如果精确匹配不足，再查基础 FAISS 知识库。
4. 如果存在上传 PDF 索引或语料，再合并上传 PDF 结果。
5. 返回带来源、文件名、页码、抽取后端和文档片段的文本块。

知识库只提供元信息和手册证据，不代表实时设备状态。实时状态必须来自 SQL 或其他运行数据证据。

### 报告工具行为

`save_report` 接收结构化报告字段：

```text
title
report_time
diagnosis_object
diagnosis_type
executive_summary
diagnosis_overview
diagnosis_details
fault_inference
repair_recommendations
preventive_maintenance
diagnosis_basis
report_filename
chart_payload
```

阶段代码会用 `build_report_payload()` 组装这些字段。报告工具负责 Markdown 子集渲染、HTML 模板、ECharts 图表数据嵌入、文件名安全处理和写入 `REPORTS_DIR`。

### 手动调用示例

常规调试优先通过 `/chat/stream` 走完整 Agent，因为这样会生成 trace、证据链和线程级 artifact：

```bash
curl -N --get "http://localhost:8000/chat/stream" \
  --data-urlencode "message=J1号机当前运行状态怎么样" \
  --data-urlencode "user_identity=游客"
```

如果只想单独验证工具，可以在 Python 里直接调用 LangChain tool：

```python
from fault_diagnosis.tools.kb_tools import query_knowledge_base

result = query_knowledge_base.invoke({"query": "F01002 故障码 含义 触发原因 处理步骤"})
print(result)
```

```python
from fault_diagnosis.diagnosis.adapters import build_sql_tools_map

tools = build_sql_tools_map()
result = tools["sql_db_query"].invoke({
    "query": "SELECT id, device_name, status, fault_code, alarm_code, create_time FROM real_data_01 ORDER BY create_time DESC, id DESC LIMIT 5"
})
print(result)
```

```python
from fault_diagnosis.tools.report_tools import save_report

result = save_report.invoke({
    "title": "测试诊断报告",
    "report_time": "2026-06-17 10:00:00",
    "diagnosis_object": "J1号机",
    "diagnosis_type": "故障诊断",
    "executive_summary": "测试摘要",
    "diagnosis_overview": "测试概览",
    "diagnosis_details": "测试详情",
    "fault_inference": "测试推断",
    "repair_recommendations": "测试维修建议",
    "preventive_maintenance": "测试预防建议",
    "diagnosis_basis": "测试依据",
    "report_filename": "debug_report",
    "chart_payload": None,
})
print(result)
```

## 证据链

证据链是当前诊断 Agent 的核心合同，数据模型定义在 `diagnosis/contracts.py`：

```text
EvidenceItem   单条事实证据，只表达来源、内容、时效、质量，不直接下结论
Claim          基于证据形成的判断，必须引用 supporting_evidence_ids
EvidenceBundle 一次任务的证据账本，包含任务信息、证据、判断、质量检查和关联产物
```

### EvidenceItem

常见来源：

- `ev_user_request`：用户原始请求。
- `ev_sql_sample_window`：SQL 返回样本窗口。
- `ev_sql_event_codes`：故障码/告警码统计。
- `ev_sql_speed_deviation`：速度偏差特征。
- `ev_sql_load_level`：负载率快照。
- `ev_sql_temperature_level`：温度快照。
- `ev_kb_001` 等：知识库手册片段。
- `ev_sql_result_missing`、`ev_kb_result_missing`：工具未返回有效证据时的缺失证据项。

EvidenceItem 的质量标签包括：

- `reliability`：来源可靠性。
- `freshness`：时效性，可能是 `current`、`recent`、`stale`、`unknown`。
- `relevance`：与任务相关性。
- `completeness`：完整性，可能是 `complete`、`partial`、`missing`。

### Claim

`evidence/claims.py` 会从 `AnalysisStepArtifact` 和 `WorkOrderSuggestion` 生成判断：

- `claim_diagnosis_summary`：最终诊断摘要。
- `claim_root_cause_001` 等：根因候选。
- `claim_risk_assessment`：风险提示。
- `claim_recommendation`：处置建议。
- `claim_workorder_decision`：是否建议生成工单。

Claim 必须包含：

- `statement`：判断文本。
- `confidence`：置信度。
- `supporting_evidence_ids`：支持证据 ID。
- `missing_evidence`：仍缺失的验证材料。
- `reasoning_summary`：短推理摘要，不保存长链路思考。
- `status`：`candidate`、`confirmed`、`rejected` 或 `final`。

### EvidenceBundle 质量检查

`evidence/quality.py` 的 `validate_evidence_bundle()` 会写入：

- `has_asset`
- `has_user_request`
- `has_current_status`
- `has_alarm_history`
- `has_manual_reference`
- `has_timeseries_feature`
- `all_claims_have_evidence`
- `no_dangling_evidence_refs`
- `dangling_evidence_refs`
- `missing_evidence_disclosed`
- `evidence_count`
- `claim_count`

判断证据链是否完整时，优先看这些结构化字段，而不是只看日志里“证据链校验完成”这类摘要。

### 输出 Guardrail

`build_output_guardrail_result()` 会检查：

- 最终回答是否为空。
- 动作请求是否出现“已重启/已停机/已关闭告警/已派发”等危险执行表述。
- Claim 是否缺少支持证据。
- Claim 是否引用不存在的 evidence id。

结果会进入 `complete.output_guardrail`、trace metadata 和保存的 artifact。

## 输出与持久化

### SSE 事件

Agent 可能发送的事件：

```text
start
task_update
ping
tool_start
tool_end
token
complete
server_error
```

其中：

- `start`：包含 thread、stream、trace 和初始 stage。
- `task_update`：前端 workflow 进度面板数据。
- `ping`：长阶段保活。
- `tool_start/tool_end`：工具输入输出、阶段、run_id、预览、可选 evidence preview。
- `token`：当前实现通常在最终回答阶段一次性发送完整回答。
- `complete`：完整结构化结果。
- `server_error`：错误分类后的兼容错误事件。

### complete payload

完整诊断 `complete` 由 `output/payloads.py` 构建，核心字段：

```text
runtime
final_content
report_filename
report_url
decision
sql_artifact
knowledge_artifact
analysis_artifact
permission_check
risk_check
resolution_recommendation
audit_log
workorder_decision
report_artifact
evidence_bundle
output_guardrail
workflow_route
workflow_policy
todos
artifact
trace
event_count
```

`build_diagnosis_contract_payload()` 会补齐前端历史兼容字段。字段名里保留 `workflow_*` 是为了兼容前端，不表示后端仍有独立 workflow runner。

### Artifact

`save_artifact` 阶段会构建并保存 `DiagnosisArtifactEnvelope`：

- `workflow_type`：通常等于任务类型，例如 `fault_diagnosis`。
- `thread_id`
- `created_at`
- `request_summary`
- `final_answer`
- `report_filename`
- `payload`：包含 request、decision、各阶段 artifact、trace、证据链、guardrail。
- `evidence`：兼容旧前端的证据数组，优先使用 EvidenceBundle 的 evidence_items。

保存位置由 `diagnosis/artifact_store.py` 和后端环境变量决定。默认文件后端会写入：

```text
trash/run/diagnosis_artifacts/*.jsonl
```

线程级 artifact 也是“基于刚才诊断生成报告”的数据来源。

## Trace 与调试

每个阶段会写 `AgentTrace` 事件：

- `stage`
- `decision`
- `tool_call`
- `tool_result`
- `artifact`
- `final_answer`

runner 同时会把关键 metadata 写入 trace exporter 和本地 trace，包括：

- `round_count`
- `tool_call_count`
- `decision`
- `workflow_policy_id`
- `primary_task_type`
- `report_filename`
- `evidence_bundle_id`
- `evidence_count`
- `claim_count`
- `evidence_quality_checks`
- `output_guardrail`

调试固定 workflow 报错时，先检查：

1. 是否超过 `max_rounds`。
2. 是否超过 `max_tool_calls`。
3. 工具名是否在本轮 `runtime_tools` 和硬白名单中。
4. SQL 是否只读、表名是否在允许列表。
5. `evidence_quality_checks` 是否有悬空引用或缺失披露。
6. `complete` payload 或保存 artifact 是否包含完整证据包。

## 扩展约定

### 新增或修改任务分类

优先改：

- `workflow/contracts.py`：新增 `TaskType` 或结构字段。
- `workflow/router.py`：新增分类关键词、对象抽取、子目标和缺失槽位。
- `workflow/policies.py`：新增或调整 policy、节点开关、证据要求、guardrail。
- `intent.py`：如果理解 payload 或规则 fallback 也需要新增字段，再同步调整。

### 调整 workflow 阶段

优先改：

- `flow.py`：阶段顺序和启停逻辑。
- `stages.py`：单个业务阶段内部实现。
- `workflow/todos.py`：前端进度分组投影。
- `contracts.py`：阶段数量变化时检查 `SingleAgentLimits.max_rounds`。

新增阶段后要重新数完整链路阶段数，避免最后在 `save_artifact` 或 `complete` 前被 `max_rounds` 截断。

### 新增工具

需要同时处理：

1. 在 `fault_diagnosis/tools/` 或合适模块实现工具。
2. 在 `workflow/policies.py` 中把对应节点映射到运行时工具名。
3. 在 `contracts.py` 的 `SingleAgentLimits.allowed_tools` 加入硬白名单。
4. 在 `stages.py` 中明确哪个阶段、什么输入调用该工具。
5. 在 `runner.py` 或 evidence 模块中补充必要的 `tool_end` evidence preview。
6. 在 `diagnosis/contracts.py` 或阶段 artifact 中定义结构化输出。
7. 补测试，至少覆盖白名单、节点启停和 artifact/complete payload。

### 修改输出字段

优先改：

- `output/payloads.py`：`complete` payload。
- `artifacts.py`：保存 artifact。
- `runtime/diagnosis_contract_adapter.py`：前端历史兼容合同。
- `diagnosis/contracts.py`：领域合同。

不要把前端兼容字段散落回 `runner.py` 或 `flow.py`。

### 修改证据链

优先改：

- `diagnosis/contracts.py`：EvidenceItem / Claim / EvidenceBundle 合同。
- `evidence/sql.py`：SQL 结果证据。
- `evidence/knowledge.py`：知识库结果证据。
- `evidence/claims.py`：判断构造。
- `evidence/quality.py`：质量检查和输出 guardrail。

保持三层语义：

```text
EvidenceItem = 事实
Claim = 判断
EvidenceBundle = 一次任务的事实账本和判断集合
```

不要把事实和判断混成一个泛化 dict。

## 验证建议

文档修改不需要跑完整测试。代码改动建议按风险选择：

```bash
PYTHONPATH=. pytest -q
python -m pytest tests
python -m compileall fault_diagnosis
git diff --check
```

涉及前端展示时，再到 `agent_fronted/` 运行：

```bash
npm run build
```

## 维护原则

- Agent 是固定、可审计、受限工具调用的诊断流水线，不要退回模型自由工具循环。
- `runner.py` 只保留运行时门面和横切能力，业务阶段放在 `stages.py` 或独立模块。
- workflow policy 决定任务路径，阶段实现负责产物质量，两者不要互相塞逻辑。
- SQL/知识库/报告工具必须有明确输入输出和安全边界。
- 诊断结论必须能回溯到 EvidenceBundle，而不是只存在最终回答文本里。
- 动作请求只能给建议、草稿、审批提示和审计信息，不直接执行设备控制、配置修改、告警关闭或工单派发。
